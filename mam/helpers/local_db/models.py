"""Local SQLite schema. Changes require an Alembic migration (`mam db revision`)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class MediaFile(Base):
    """One media file: an original found on the NAS, or a file derived from one."""

    __tablename__ = "media_file"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    size: Mapped[int]
    mtime: Mapped[float]
    kind: Mapped[str] = mapped_column(String(8))  # audio | video
    original_path: Mapped[str | None] = mapped_column(Text, unique=True)
    processed_path: Mapped[str | None] = mapped_column(Text, index=True)
    current_name: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("media_file.id"), index=True)
    derivation: Mapped[str | None] = mapped_column(String(32))  # kirtan | class | trimmed | ...
    status: Mapped[str] = mapped_column(String(64), default="Not processed", index=True)
    baserow_row_id: Mapped[int | None] = mapped_column(Integer, index=True)
    # Display fields filled by the tools (the full proposal and evidence live in `meta`).
    rec_date: Mapped[str | None] = mapped_column(
        String(10), index=True
    )  # YYYY-MM-DD, may hold placeholders
    title: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64), index=True)
    meta: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    parent: Mapped[MediaFile | None] = relationship(remote_side=[id])
    aliases: Mapped[list[FileAlias]] = relationship(back_populates="media_file")


class FileAlias(Base):
    """Another original path holding byte-identical content."""

    __tablename__ = "file_alias"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_file_id: Mapped[int] = mapped_column(ForeignKey("media_file.id"), index=True)
    path: Mapped[str] = mapped_column(Text, unique=True)
    media_file: Mapped[MediaFile] = relationship(back_populates="aliases")


class FileEvent(Base):
    """Every rename, copy, move or write, including dry runs."""

    __tablename__ = "file_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_file_id: Mapped[int | None] = mapped_column(ForeignKey("media_file.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))  # copy | move | rename | write | delete
    tool: Mapped[str] = mapped_column(String(64))
    src: Mapped[str | None] = mapped_column(Text)
    dst: Mapped[str | None] = mapped_column(Text)
    dry_run: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[dict[str, Any] | None]
    operator: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StatusHistory(Base):
    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_file_id: Mapped[int] = mapped_column(ForeignKey("media_file.id"), index=True)
    old_status: Mapped[str | None] = mapped_column(String(64))
    new_status: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WEvidence(Base):
    """A candidate value for one of WHEN/WHO/WHAT/WHERE and where it came from."""

    __tablename__ = "w_evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_file_id: Mapped[int] = mapped_column(ForeignKey("media_file.id"), index=True)
    field: Mapped[str] = mapped_column(String(16))  # when | who | what | where
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(
        String(32)
    )  # filename | folder | tag | media | travel | ...
    score: Mapped[float] = mapped_column(Float)
    tool: Mapped[str] = mapped_column(String(64))
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    detail: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    """Persisted queue entry; survives restarts."""

    __tablename__ = "job"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_file_id: Mapped[int | None] = mapped_column(ForeignKey("media_file.id"), index=True)
    tool: Mapped[str] = mapped_column(String(64))
    pool: Mapped[str] = mapped_column(String(8))  # ml | cpu | io
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    pipeline: Mapped[bool] = mapped_column(Boolean, default=False)
    params: Mapped[dict[str, Any] | None]
    result: Mapped[dict[str, Any] | None]
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Review(Base):
    """A flagged decision: the tool's proposal and the reviewer's outcome."""

    __tablename__ = "review"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_file_id: Mapped[int] = mapped_column(ForeignKey("media_file.id"), index=True)
    tool: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    proposal: Mapped[dict[str, Any]]
    features: Mapped[dict[str, Any] | None]
    score: Mapped[float | None] = mapped_column(Float)
    spot_check: Mapped[bool] = mapped_column(Boolean, default=False)
    # pending | approved | edited | rejected | excluded
    outcome: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    final: Mapped[dict[str, Any] | None]
    operator: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BaserowWriteLog(Base):
    """Every Baserow create/update/delete with before and after, for undo."""

    __tablename__ = "baserow_write_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_file_id: Mapped[int | None] = mapped_column(ForeignKey("media_file.id"), index=True)
    table_id: Mapped[int] = mapped_column(Integer)
    row_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(16))  # create | update | delete | undo
    before: Mapped[dict[str, Any] | None]
    after: Mapped[dict[str, Any] | None]
    payload: Mapped[dict[str, Any] | None]
    dry_run: Mapped[bool] = mapped_column(Boolean)
    tool: Mapped[str] = mapped_column(String(64))
    undone: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segment"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(8))
    confidence: Mapped[float | None] = mapped_column(Float)
    words: Mapped[list[Any] | None]
    speaker: Mapped[str | None] = mapped_column(String(32))
    section: Mapped[str | None] = mapped_column(String(32))


class TranscriptRun(Base):
    """Binding metadata for one transcription (engine, version, model hash, backend)."""

    __tablename__ = "transcript_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    content_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    engine: Mapped[str] = mapped_column(String(32))
    engine_version: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    model_hash: Mapped[str | None] = mapped_column(String(64))
    backend: Mapped[str] = mapped_column(String(16))
    fallback: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VedabaseCache(Base):
    __tablename__ = "vedabase_cache"

    url: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict[str, Any] | None]
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LlmCache(Base):
    __tablename__ = "llm_cache"

    key: Mapped[str] = mapped_column(
        String(64), primary_key=True
    )  # sha256(input hash + prompt version)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(32))
    response: Mapped[dict[str, Any]]
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VoiceProfile(Base):
    __tablename__ = "voice_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), default="KKS")
    model: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[list[Any]]
    sources: Mapped[list[Any]]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Calibration(Base):
    """Learned auto/review threshold for one tool decision type."""

    __tablename__ = "calibration"

    id: Mapped[int] = mapped_column(primary_key=True)
    tool: Mapped[str] = mapped_column(String(64), index=True)
    decision: Mapped[str] = mapped_column(String(32))
    threshold: Mapped[float] = mapped_column(Float)
    n_examples: Mapped[int] = mapped_column(Integer)
    precision: Mapped[float | None] = mapped_column(Float)
    auto_rate: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSetting(Base):
    """A setting changed in the media manager (e.g. the Baserow writes switch)."""

    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]]  # {"value": ...}
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AliasRule(Base):
    """A learned filename correction, e.g. token 'noi' -> 'no' for WHERE."""

    __tablename__ = "alias_rule"

    id: Mapped[int] = mapped_column(primary_key=True)
    field: Mapped[str] = mapped_column(String(16))
    token: Mapped[str] = mapped_column(String(128))
    replacement: Mapped[str] = mapped_column(String(128))
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
