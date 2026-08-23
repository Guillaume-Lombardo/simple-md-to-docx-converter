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
