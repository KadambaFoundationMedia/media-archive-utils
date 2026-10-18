"""File list filtering, sorting and paging."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from mam.helpers.local_db import models as m

PAGE_SIZE = 50
DEFAULT_STATUS = "__attention"  # the file list opens on the files that need a person
ATTENTION_LIKE = ("% Review", "% Failed")
SORTS = {
    "path": m.MediaFile.original_path,
    "name": m.MediaFile.current_name,
    "date": m.MediaFile.rec_date,
    "status": m.MediaFile.status,
    "updated": m.MediaFile.updated_at,
    "category": m.MediaFile.category,
}


@dataclass
class FileFilter:
    q: str = ""
    status: str = "__attention"
    kind: str = ""
    category: str = ""
    folder: str = ""
    sort: str = "path"
    desc: bool = False
    page: int = 1
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_params(cls, params: Any, default_status: str = DEFAULT_STATUS) -> FileFilter:
        def get(name: str, default: str = "") -> str:
            return (params.get(name) or default).strip()

        try:
            page = max(1, int(get("page", "1")))
        except ValueError:
            page = 1
        sort = get("sort", "path")
        return cls(
            q=get("q"),
            status=get("status", default_status),
            kind=get("kind"),
            category=get("category"),
            folder=get("folder"),
            sort=sort if sort in SORTS else "path",
            desc=get("desc") in ("1", "true", "on"),
            page=page,
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
            "page": self.page,
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
                )
            )
        if self.status == "__attention":
            stmt = stmt.where(
                or_(
                    *(m.MediaFile.status.like(p) for p in ATTENTION_LIKE),
                    m.MediaFile.status == "Corrupted",
                )
            )
        elif self.status == "__all":
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
            stmt = stmt.where(m.MediaFile.category == self.category)
        if self.folder:
            stmt = stmt.where(m.MediaFile.original_path.like(f"{self.folder.rstrip('/')}/%"))
        return stmt


def list_files(session: Session, flt: FileFilter) -> tuple[list[m.MediaFile], int]:
    base = flt.apply(select(m.MediaFile).where(m.MediaFile.parent_id.is_(None)))
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    column = SORTS[flt.sort]
    order = [column.desc() if flt.desc else column.asc(), m.MediaFile.id]
    rows = session.scalars(
        base.order_by(*order).offset((flt.page - 1) * PAGE_SIZE).limit(PAGE_SIZE)
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
        select(m.MediaFile.category)
        .where(m.MediaFile.category.is_not(None))
        .distinct()
        .order_by(m.MediaFile.category)
    )
    counts = dict(statuses)
    attention = sum(
        n for s, n in counts.items() if s.endswith((" Review", " Failed")) or s == "Corrupted"
    )
    return {
        "statuses": [(s, counts.get(s, 0)) for s in all_statuses(counts)],
        "categories": list(categories),
        "attention": attention,
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
