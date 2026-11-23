"""shorter status names; Kirtan splitter statuses that say what it found

`Queued <Tool>` and `Processing <Tool>` become `Queued` and `Processing`. A recording the
Kirtan splitter handled has `Skipped` or `Kirtan splitter Done`; they become
`Splitter: Class`, `: Kirtan` or `: Split` from the splitter's stored proposal, and a
kirtan recording is marked so the Renamer files it under Kirtan.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-23 12:00:00
"""
import json

from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def _meta(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return {}
    return raw or {}


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE media_file SET status='Queued' WHERE status LIKE 'Queued %'"))
    conn.execute(
        sa.text(
            "UPDATE media_file SET status='Processing' "
            "WHERE status LIKE 'Processing %' AND status != 'Processing complete'"
        )
    )
    conn.execute(
        sa.text(
            "UPDATE media_file SET status='Splitter: Done' "
            "WHERE status='Kirtan splitter Done'"
        )
    )
    rows = conn.execute(
        sa.text("SELECT id, meta FROM media_file WHERE status='Skipped'")
    ).fetchall()
    for row_id, raw in rows:
        meta = _meta(raw)
        mode = ((meta.get("splitter") or {}).get("proposal") or {}).get("mode")
        if mode not in ("class_only", "kirtan_only"):
            continue
        status = "Splitter: Class" if mode == "class_only" else "Splitter: Kirtan"
        if mode == "kirtan_only":
            meta["recording_kind"] = "kirtan"
        conn.execute(
            sa.text("UPDATE media_file SET status=:s, meta=:m WHERE id=:i"),
            {"s": status, "m": json.dumps(meta), "i": row_id},
        )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("UPDATE media_file SET status='Kirtan splitter Done' "
                "WHERE status='Splitter: Done'")
    )
    conn.execute(
        sa.text("UPDATE media_file SET status='Skipped' "
                "WHERE status IN ('Splitter: Class', 'Splitter: Kirtan')")
    )
