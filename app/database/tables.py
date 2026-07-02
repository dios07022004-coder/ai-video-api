"""ORM table definitions.

Schema is intentionally provider-neutral (works on SQLite and PostgreSQL). JSON
columns use SQLAlchemy's generic ``JSON`` type. Money/credits are integers
(smallest unit) to avoid float drift.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.constants import BillingEntryType, TaskType
from app.database.base import Base, TimestampMixin
from app.models.enums import TaskStatus


class Partner(Base, TimestampMixin):
    """A website/partner integrating the API. Owns API keys, credits and tasks."""

    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    balance_credits: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="partner", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="partner")


class ApiKey(Base, TimestampMixin):
    """Hashed API key. The plaintext is shown once at creation and never stored."""

    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_prefix", "prefix"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    partner_id: Mapped[int] = mapped_column(
        ForeignKey("partners.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Fast-lookup prefix + constant-time compared SHA-256 of the full key.
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    partner: Mapped[Partner] = relationship(back_populates="api_keys")


class Upload(Base, TimestampMixin):
    """A validated user image made available to ComfyUI as workflow input."""

    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4
    partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("partners.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    comfy_name: Mapped[str] = mapped_column(String(255), nullable=False)  # name in Comfy input/
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class Task(Base, TimestampMixin):
    """A single generation job across its full lifecycle."""

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("partner_id", "request_id", name="uq_tasks_idempotency"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_partner_created", "partner_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # uuid4 task_id
    partner_id: Mapped[int | None] = mapped_column(
        ForeignKey("partners.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Partner-supplied user + idempotency key.
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    task_type: Mapped[str] = mapped_column(String(16), default=TaskType.VIDEO, nullable=False)
    mode: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=TaskStatus.QUEUED, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0..100

    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    negative_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    callback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Fully-resolved parameter set actually injected into the workflow.
    resolved_params: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    request_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    price_credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # ComfyUI correlation
    comfy_prompt_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    comfy_endpoint: Mapped[str | None] = mapped_column(String(128), nullable=True)

    result_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    result_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    partner: Mapped[Partner | None] = relationship(back_populates="tasks")


class BillingEntry(Base, TimestampMixin):
    """Append-only credit ledger. Balance = sum(amount). Never mutated in place."""

    __tablename__ = "billing_entries"
    __table_args__ = (Index("ix_billing_partner_created", "partner_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    partner_id: Mapped[int] = mapped_column(
        ForeignKey("partners.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    entry_type: Mapped[str] = mapped_column(String(16), default=BillingEntryType.CHARGE, nullable=False)
    # Signed: charges/holds negative, topups/refunds positive.
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class EventLog(Base, TimestampMixin):
    """Structured audit trail persisted for admin visibility (in addition to files)."""

    __tablename__ = "event_logs"
    __table_args__ = (Index("ix_event_logs_task", "task_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    partner_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO", nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="api", nullable=False)
    event: Mapped[str] = mapped_column(String(120), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
