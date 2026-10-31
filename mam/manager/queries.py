"""File list filtering, sorting and batch loading."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from mam.helpers.local_db import models as m

PAGE_SIZES = (50, 100, 200, 500)  # the "Show" choices; Load more adds this many each time
PAGE_SIZE = PAGE_SIZES[0]
MAX_SHOWN = 20000  # upper bound for re-rendering the already loaded rows
DEFAULT_STATUS = "__all"  # the file list opens on this unless Settings chooses another
LEGACY_STATUS = {"__attention": "__review"}  # old bookmarks of the removed view
VIEWS = ("__all", "__review", "__failed")  # the views above the single statuses


def _proposal(field: str):
    """A field of the last Renamer proposal (a dry run stores it before anything is renamed)."""
    return m.MediaFile.meta["proposal"][field].as_string()


# The category shown in the list: the saved one, else the one a dry run suggested.
category_expr = func.coalesce(func.nullif(m.MediaFile.category, ""), _proposal("category"))
SORTS = {
    "path": m.MediaFile.original_path,
    "name": m.MediaFile.current_name,
    "date": m.MediaFile.rec_date,
    "status": m.MediaFile.status,
    "updated": m.MediaFile.updated_at,
    "category": category_expr,
}


@dataclass
class FileFilter:
    q: str = ""
    status: str = DEFAULT_STATUS
    kind: str = ""
    category: str = ""
    folder: str = ""
    sort: str = "path"
    desc: bool = False
    size: int = PAGE_SIZE
    offset: int = 0  # rows already shown; Load more continues here
    limit: int = 0  # re-render this many rows from the top (used while polling)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_params(cls, params: Any, default_status: str = DEFAULT_STATUS) -> FileFilter:
        def get(name: str, default: str = "") -> str:
            return (params.get(name) or default).strip()

        def number(name: str) -> int:
            try:
                return max(0, min(int(get(name, "0")), MAX_SHOWN))
            except ValueError:
                return 0

        size = number("size")
        sort = get("sort", "path")
        return cls(
            q=get("q"),
            status=LEGACY_STATUS.get(get("status", default_status), get("status", default_status)),
            kind=get("kind"),
            category=get("category"),
            folder=get("folder"),
            sort=sort if sort in SORTS else "path",
            desc=get("desc") in ("1", "true", "on"),
            size=size if size in PAGE_SIZES else PAGE_SIZE,
            offset=number("offset"),
            limit=number("limit"),
        )

    def query_args(self, **overrides: Any) -> dict[str, Any]:
        args = {
            "q": self.q,
            "status": self.status,
            "kind": self.kind,
            "category": self.category,
            "folder": self.folder,
            "sort": self.sort,
            "desc": "1" if self.desc else "",
            "size": self.size,
        }
        args.update(overrides)
        return {k: v for k, v in args.items() if v not in ("", None)}

    def apply(self, stmt: Select) -> Select:
        if self.q:
            like = f"%{self.q}%"
            stmt = stmt.where(
                or_(
                    m.MediaFile.original_path.ilike(like),
                    m.MediaFile.current_name.ilike(like),
                    m.MediaFile.title.ilike(like),
                    m.MediaFile.location.ilike(like),
                    _proposal("new_name").ilike(like),
                    _proposal("what").ilike(like),
                    _proposal("where").ilike(like),
                )
            )
        if self.status == "__all":
            pass
        elif self.status == "__review":
            stmt = stmt.where(m.MediaFile.status.like("% Review"))
        elif self.status == "__failed":
            stmt = stmt.where(m.MediaFile.status.like("% Failed"))
        elif self.status:
            stmt = stmt.where(m.MediaFile.status == self.status)
        if self.kind:
            stmt = stmt.where(m.MediaFile.kind == self.kind)
        if self.category:
            stmt = stmt.where(category_expr == self.category)
        if self.folder:
            stmt = stmt.where(m.MediaFile.original_path.like(f"{self.folder.rstrip('/')}/%"))
        return stmt


def list_files(session: Session, flt: FileFilter) -> tuple[list[m.MediaFile], int]:
    base = flt.apply(select(m.MediaFile).where(m.MediaFile.parent_id.is_(None)))
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    column = SORTS[flt.sort]
    order = [column.desc() if flt.desc else column.asc(), m.MediaFile.id]
    rows = session.scalars(
        base.order_by(*order).offset(flt.offset).limit(flt.limit or flt.size)
    ).all()
    return list(rows), total


def matching_ids(session: Session, flt: FileFilter) -> list[int]:
    stmt = flt.apply(select(m.MediaFile.id).where(m.MediaFile.parent_id.is_(None)))
    return list(session.scalars(stmt))


def all_statuses(present: dict[str, int] | None = None) -> list[str]:
    """Every status a file can have, in the order of the status guide (SPEC.md section 7).

    Per-tool statuses are listed for the pipeline tools that are built; a status in the
    database that is not in this list (an older tool name) is added at the end.
    """
    from mam.manager.tools import PIPELINE_ORDER, TOOLS

    ordered = ["Not processed"]
    for name in PIPELINE_ORDER:
        if name in TOOLS:
            label = TOOLS[name].label
            ordered += [f"Queued {label}", f"Processing {label}"]
            ordered += [f"{label} {state}" for state in ("Done", "Review", "Failed")]
    ordered += ["Skipped", "Excluded", "Corrupted", "Processing complete"]
    return ordered + sorted(set(present or {}) - set(ordered))


def facet_values(session: Session) -> dict[str, Any]:
    statuses = session.execute(
        select(m.MediaFile.status, func.count(m.MediaFile.id))
        .where(m.MediaFile.parent_id.is_(None))
        .group_by(m.MediaFile.status)
        .order_by(m.MediaFile.status)
    ).all()
    categories = session.scalars(
        select(category_expr)
        .where(m.MediaFile.parent_id.is_(None), category_expr.is_not(None))
        .distinct()
        .order_by(category_expr)
    )
    counts = dict(statuses)
    return {
        "statuses": [(s, counts.get(s, 0)) for s in all_statuses(counts)],
        "categories": list(categories),
        "review": sum(n for s, n in counts.items() if s.endswith(" Review")),
        "failed": sum(n for s, n in counts.items() if s.endswith(" Failed")),
        "all": sum(counts.values()),
    }


def counts(session: Session) -> dict[str, int]:
    def count(*where: Any) -> int:
        return session.scalar(select(func.count(m.MediaFile.id)).where(*where)) or 0

    active = session.scalar(
        select(func.count(m.Job.id)).where(m.Job.state.in_(("queued", "running")))
    )
    return {
        "files": count(m.MediaFile.parent_id.is_(None)),
        "review": count(m.MediaFile.status.like("% Review")),
        "failed": count(m.MediaFile.status.like("% Failed")),
        "jobs": active or 0,
    }


VIEW_LABELS = {"__all": "All files", "__review": "Review", "__failed": "Failed"}


def status_choices(counts: dict[str, int] | None = None) -> list[tuple[str, str, str]]:
    """The Status choices as (value, label, group): the views, then every single status.

    Used by the file list filter and by the default status on the Settings page.
    """
    return [(v, VIEW_LABELS[v], "") for v in VIEWS] + [
        (s, s, "One status") for s in all_statuses(counts)
    ]
