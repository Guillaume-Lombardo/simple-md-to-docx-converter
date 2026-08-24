"""SQLAlchemy schema shared by SQLite and PostgreSQL."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative metadata root used by Alembic."""


class UserRow(Base):
    """Persistent local account row."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    normalized_username: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    password_hash: Mapped[str] = mapped_column(String(1024), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SessionRow(Base):
    """Digest-only persistent authentication session row."""

    __tablename__ = "sessions"

    token_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index("ix_sessions_user_id", SessionRow.user_id)


class TemplateRow(Base):
    """Stable template identity; content versions arrive in T15."""

    __tablename__ = "templates"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_templates_status"),
        CheckConstraint(
            "normalized_name <> ''", name="ck_templates_normalized_name_nonempty"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(), nullable=False)
    description: Mapped[str] = mapped_column(String(), nullable=False)
    normalized_description: Mapped[str] = mapped_column(String(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class TemplatePreferenceRow(Base):
    """At most one preferred template per local account."""

    __tablename__ = "template_preferences"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="RESTRICT"), nullable=False
    )


class SystemTemplateSelectionRow(Base):
    """Singleton system fallback selected by an administrator."""

    __tablename__ = "system_template_selection"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_system_template_selection_singleton"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fallback_template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="RESTRICT"), nullable=False
    )


Index("ix_templates_owner_id", TemplateRow.owner_id)
Index("ix_templates_status", TemplateRow.status)
Index("ix_templates_search_order", TemplateRow.normalized_name, TemplateRow.id)
Index("ix_template_preferences_template_id", TemplatePreferenceRow.template_id)


class ConversionJobRow(Base):
    """Durable queue row shared by embedded and external workers."""

    __tablename__ = "conversion_jobs"
    __table_args__ = (
        CheckConstraint(
            "output IN ('docx', 'pdf', 'both')", name="ck_conversion_jobs_output"
        ),
        CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', "
            "'expired')",
            name="ck_conversion_jobs_state",
        ),
        CheckConstraint(
            "step IN ('queued', 'validating', 'rendering', 'docx', 'pdf', "
            "'publishing', 'complete')",
            name="ck_conversion_jobs_step",
        ),
        CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="ck_conversion_jobs_progress",
        ),
        CheckConstraint("attempt >= 0", name="ck_conversion_jobs_attempt"),
        UniqueConstraint(
            "owner_id",
            "idempotency_digest",
            name="uq_conversion_jobs_owner_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    source_object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    template_id: Mapped[str] = mapped_column(String(36), nullable=False)
    template_version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    output: Mapped[str] = mapped_column(String(16), nullable=False)
    component_versions: Mapped[str] = mapped_column(String(), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    step: Mapped[str] = mapped_column(String(32), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_digest: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_ready: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    result_object_id: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(String(1024))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    cleanup_owner: Mapped[str | None] = mapped_column(String(255))
    cleanup_token: Mapped[str | None] = mapped_column(String(36))
    cleanup_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_conversion_jobs_queue",
    ConversionJobRow.state,
    ConversionJobRow.created_at,
    ConversionJobRow.id,
)
Index(
    "ix_conversion_jobs_owner_created",
    ConversionJobRow.owner_id,
    ConversionJobRow.created_at,
    ConversionJobRow.id,
)
Index(
    "ix_conversion_jobs_lease_expiry",
    ConversionJobRow.state,
    ConversionJobRow.lease_expires_at,
)
Index(
    "ix_conversion_jobs_terminal_expiry",
    ConversionJobRow.state,
    ConversionJobRow.expires_at,
)
Index(
    "ix_conversion_jobs_cleanup",
    ConversionJobRow.state,
    ConversionJobRow.cleanup_completed,
    ConversionJobRow.cleanup_expires_at,
)
