"""SQLAlchemy schema shared by SQLite and PostgreSQL."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from markweave.auth.models import USERNAME_MAX_LENGTH


class Base(DeclarativeBase):
    """Declarative metadata root used by Alembic."""


class UserRow(Base):
    """Persistent local account row."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(USERNAME_MAX_LENGTH), nullable=False)
    normalized_username: Mapped[str] = mapped_column(
        String(USERNAME_MAX_LENGTH), nullable=False, unique=True
    )
    password_hash: Mapped[str] = mapped_column(String(1024), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    password_change_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


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


class IdleSessionPolicyRow(Base):
    """Optional singleton administrator override for role idle durations."""

    __tablename__ = "idle_session_policy"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_idle_session_policy_singleton"),
        CheckConstraint(
            "user_idle_minutes BETWEEN 5 AND 300",
            name="ck_idle_session_policy_user_minutes",
        ),
        CheckConstraint(
            "admin_idle_minutes BETWEEN 5 AND 60",
            name="ck_idle_session_policy_admin_minutes",
        ),
        CheckConstraint("revision > 0", name="ck_idle_session_policy_revision"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_idle_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    admin_idle_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)


class IdleSessionPolicyAuditRow(Base):
    """Immutable role policy old/new values and resulting revision."""

    __tablename__ = "idle_session_policy_audit_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    old_user_idle_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    old_admin_idle_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    new_user_idle_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    new_admin_idle_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index(
    "ix_idle_session_policy_audit_retention",
    IdleSessionPolicyAuditRow.created_at,
    IdleSessionPolicyAuditRow.id,
)


class AuthenticationAuditRow(Base):
    """Durable content-free audit trail for sensitive account mutations."""

    __tablename__ = "authentication_audit_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False)
    administrator_intervention: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index(
    "ix_authentication_audit_target",
    AuthenticationAuditRow.target_id,
    AuthenticationAuditRow.created_at,
)
Index(
    "ix_authentication_audit_retention",
    AuthenticationAuditRow.created_at,
    AuthenticationAuditRow.id,
)


class TemplateRow(Base):
    """Stable template identity; content versions arrive in T15."""

    __tablename__ = "templates"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived')", name="ck_templates_status"),
        CheckConstraint(
            "publication_state IN ('pending', 'published', 'deleting')",
            name="ck_templates_publication_state",
        ),
        CheckConstraint(
            "normalized_name <> ''", name="ck_templates_normalized_name_nonempty"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    kind: Mapped[str] = mapped_column(
        String(8),
        CheckConstraint("kind IN ('docx', 'pptx')", name="ck_templates_kind"),
        nullable=False,
        server_default="docx",
    )
    name: Mapped[str] = mapped_column(String(), nullable=False)
    normalized_name: Mapped[str] = mapped_column(String(), nullable=False)
    description: Mapped[str] = mapped_column(String(), nullable=False)
    normalized_description: Mapped[str] = mapped_column(String(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_version_id: Mapped[str | None] = mapped_column(String(36))
    publication_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="published"
    )


class TemplateVersionRow(Base):
    """Immutable template content metadata pointing at an object-store key."""

    __tablename__ = "template_versions"
    __table_args__ = (
        CheckConstraint("version_number > 0", name="ck_template_versions_number"),
        CheckConstraint("size > 0", name="ck_template_versions_size"),
        UniqueConstraint(
            "template_id", "version_number", name="uq_template_versions_number"
        ),
        UniqueConstraint("template_id", "id", name="uq_template_versions_pair"),
        CheckConstraint(
            "publication_state IN ('pending', 'published')",
            name="ck_template_versions_publication_state",
        ),
        CheckConstraint(
            "publication_state = 'published' OR "
            "(publication_token IS NOT NULL AND "
            "publication_lease_expires_at IS NOT NULL)",
            name="ck_template_versions_pending_lease",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("templates.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    object_owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    restored_from_version_id: Mapped[str | None] = mapped_column(String(36))
    declared_fonts: Mapped[str] = mapped_column(String(), nullable=False)
    resolved_fonts: Mapped[str] = mapped_column(String(), nullable=False)
    validation_trace: Mapped[str] = mapped_column(String(), nullable=False)
    publication_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    publication_token: Mapped[str | None] = mapped_column(String(36))
    publication_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    retention_token: Mapped[str | None] = mapped_column(String(36))
    retention_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class TemplateAuditRow(Base):
    """Durable content-free audit trail for sensitive template mutations."""

    __tablename__ = "template_audit_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    template_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    version_id: Mapped[str | None] = mapped_column(String(36))
    administrator_intervention: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class RetentionCleanupRunRow(Base):
    """Content-free durable evidence for one bounded retention transaction."""

    __tablename__ = "retention_cleanup_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    cutoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    removed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


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
Index(
    "ix_template_versions_template_number",
    TemplateVersionRow.template_id,
    TemplateVersionRow.version_number,
)
Index(
    "ix_template_audit_target",
    TemplateAuditRow.template_id,
    TemplateAuditRow.created_at,
)
Index("ix_template_audit_retention", TemplateAuditRow.created_at, TemplateAuditRow.id)
Index(
    "ix_template_version_retention",
    TemplateVersionRow.created_at,
    TemplateVersionRow.retention_lease_expires_at,
)


class ConversionJobRow(Base):
    """Durable queue row shared by embedded and external workers."""

    __tablename__ = "conversion_jobs"
    __table_args__ = (
        CheckConstraint(
            "output IN ('docx', 'pdf', 'both', 'pptx', 'pptx-bundle')",
            name="ck_conversion_jobs_output",
        ),
        CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', "
            "'expired')",
            name="ck_conversion_jobs_state",
        ),
        CheckConstraint(
            "step IN ('queued', 'validating', 'rendering', 'docx', 'pdf', 'pptx', "
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
        CheckConstraint(
            "(template_id IS NULL) = (template_version_id IS NULL)",
            name="ck_conversion_jobs_template_pair",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    source_object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(255))
    source_kind: Mapped[str | None] = mapped_column(String(16))
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    source_size: Mapped[int | None] = mapped_column(Integer)
    template_id: Mapped[str | None] = mapped_column(String(36))
    template_version_id: Mapped[str | None] = mapped_column(String(36))
    output: Mapped[str] = mapped_column(String(16), nullable=False)
    presentation_options: Mapped[str | None] = mapped_column(String())
    component_versions: Mapped[str] = mapped_column(String(), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(128))
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
    result_manifest_object_id: Mapped[str | None] = mapped_column(String(36))
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


class ReversionJobRow(Base):
    """Distinct durable reverse-conversion queue row."""

    __tablename__ = "reversion_jobs"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', "
            "'expired')",
            name="ck_reversion_jobs_state",
        ),
        CheckConstraint(
            "step IN ('queued', 'isolating', 'converting', 'validating', "
            "'publishing', 'complete')",
            name="ck_reversion_jobs_step",
        ),
        CheckConstraint("attempt >= 0", name="ck_reversion_jobs_attempt"),
        CheckConstraint("source_size > 0", name="ck_reversion_jobs_source_size"),
        CheckConstraint(
            "source_family IN ('word', 'powerpoint', 'excel', 'opendocument', "
            "'rtf', 'epub', 'csv', 'pdf')",
            name="ck_reversion_jobs_source_family",
        ),
        CheckConstraint(
            "(state = 'running' AND lease_owner IS NOT NULL AND lease_token IS NOT "
            "NULL AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL "
            "AND current_attempt_id IS NOT NULL) OR (state <> 'running' AND "
            "lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS "
            "NULL AND heartbeat_at IS NULL AND current_attempt_id IS NULL)",
            name="ck_reversion_jobs_lease_bundle",
        ),
        CheckConstraint(
            "(state = 'succeeded' AND result_mode IS NOT NULL AND result_object_id "
            "IS NOT NULL AND result_sha256 IS NOT NULL AND result_size > 0 AND "
            "trace_metadata IS NOT NULL) OR (state <> 'succeeded' AND result_mode "
            "IS NULL AND result_object_id IS NULL AND result_sha256 IS NULL AND "
            "result_size IS NULL AND trace_metadata IS NULL)",
            name="ck_reversion_jobs_result_bundle",
        ),
        CheckConstraint(
            "(state = 'failed' AND error_code IS NOT NULL AND error_message IS NOT "
            "NULL) OR (state <> 'failed' AND error_code IS NULL AND error_message "
            "IS NULL)",
            name="ck_reversion_jobs_error_bundle",
        ),
        UniqueConstraint(
            "owner_id",
            "idempotency_digest",
            name="uq_reversion_jobs_owner_idempotency",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    source_object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_stem: Mapped[str] = mapped_column(String(255), nullable=False)
    source_extension: Mapped[str] = mapped_column(String(16), nullable=False)
    source_family: Mapped[str] = mapped_column(String(32), nullable=False)
    detected_format: Mapped[str | None] = mapped_column(String(32))
    parser_format: Mapped[str] = mapped_column(String(32), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    component_versions: Mapped[str] = mapped_column(String(), nullable=False)
    options: Mapped[str] = mapped_column(String(), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_digest: Mapped[str | None] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    step: Mapped[str] = mapped_column(String(32), nullable=False)
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
    current_attempt_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    result_mode: Mapped[str | None] = mapped_column(String(48))
    result_object_id: Mapped[str | None] = mapped_column(String(36))
    result_sha256: Mapped[str | None] = mapped_column(String(64))
    result_size: Mapped[int | None] = mapped_column(BigInteger)
    trace_metadata: Mapped[str | None] = mapped_column(String())
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(String(1024))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleanup_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    cleanup_owner: Mapped[str | None] = mapped_column(String(255))
    cleanup_token: Mapped[str | None] = mapped_column(String(36))
    cleanup_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReversionBrokerPrincipalRow(Base):
    """Durable monotone create-sequence high-water mark per broker principal."""

    __tablename__ = "reversion_broker_principals"
    __table_args__ = (
        CheckConstraint(
            "create_sequence_high_water >= 0 AND create_sequence_high_water <= 9223372036854775807",
            name="ck_reversion_broker_principals_high_water",
        ),
        CheckConstraint(
            "reconciliation_cursor >= 0 AND reconciliation_cursor <= 9223372036854775807",
            name="ck_reversion_broker_principals_cursor",
        ),
        CheckConstraint(
            "reconciliation_observed_head IS NULL OR (reconciliation_observed_head >= 0 AND reconciliation_observed_head <= 9223372036854775807)",
            name="ck_reversion_broker_principals_observed_head",
        ),
        CheckConstraint(
            "reconciliation_cursor <= create_sequence_high_water",
            name="ck_reversion_broker_principals_cursor_high_water",
        ),
        CheckConstraint(
            "reconciliation_observed_head IS NULL OR reconciliation_observed_head >= reconciliation_cursor",
            name="ck_reversion_broker_principals_observed_cursor",
        ),
        CheckConstraint(
            "NOT reconciliation_fixed_point OR reconciliation_observed_head IS NOT NULL",
            name="ck_reversion_broker_principals_fixed_point",
        ),
        CheckConstraint(
            "(reconciliation_owner IS NULL AND reconciliation_token IS NULL AND reconciliation_expires_at IS NULL) OR "
            "(reconciliation_owner IS NOT NULL AND reconciliation_token IS NOT NULL AND reconciliation_expires_at IS NOT NULL)",
            name="ck_reversion_broker_principals_reconciliation_lease",
        ),
    )

    principal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    create_sequence_high_water: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0
    )
    reconciliation_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    reconciliation_owner: Mapped[str | None] = mapped_column(String(255))
    reconciliation_token: Mapped[str | None] = mapped_column(String(36))
    reconciliation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    reconciliation_cursor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    reconciliation_observed_head: Mapped[int | None] = mapped_column(BigInteger)
    reconciliation_fixed_point: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )


class ReversionAttemptRow(Base):
    """Retained mutable-current attempt row; a new row is appended per claim."""

    __tablename__ = "reversion_attempts"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "attempt_number", name="uq_reversion_attempts_job_number"
        ),
        UniqueConstraint(
            "principal_id",
            "create_sequence",
            name="uq_reversion_attempts_principal_sequence",
        ),
        UniqueConstraint("unit_id", name="uq_reversion_attempts_unit"),
        UniqueConstraint("proof_id", name="uq_reversion_attempts_proof"),
        CheckConstraint("attempt_number > 0", name="ck_reversion_attempts_number"),
        CheckConstraint("create_sequence > 0", name="ck_reversion_attempts_sequence"),
        CheckConstraint(
            "(create_intent_at IS NULL AND policy_revision IS NULL AND "
            "policy_specification IS NULL AND unit_id IS NULL) OR "
            "(create_intent_at IS NOT NULL AND policy_revision IS NOT NULL AND "
            "policy_specification IS NOT NULL)",
            name="ck_reversion_attempts_create_intent",
        ),
        CheckConstraint(
            "(proof_id IS NULL AND proof_unit_id IS NULL AND proof_principal_id IS "
            "NULL AND proof_policy_revision IS NULL AND exit_evidence IS NULL AND "
            "empty_evidence IS NULL AND removal_evidence IS NULL AND "
            "proof_recorded_at IS NULL AND proof_acknowledged_at IS NULL) OR "
            "(proof_id IS NOT NULL AND proof_unit_id IS NOT NULL AND "
            "proof_principal_id IS NOT NULL AND proof_policy_revision IS NOT NULL "
            "AND exit_evidence IS NOT NULL AND empty_evidence IS NOT NULL AND "
            "removal_evidence IS NOT NULL AND proof_recorded_at IS NOT NULL)",
            name="ck_reversion_attempts_proof_bundle",
        ),
        CheckConstraint(
            "proof_recovery_token IS NULL OR proof_id IS NOT NULL",
            name="ck_reversion_attempts_proof_recovery_token",
        ),
        CheckConstraint(
            "(recovery_owner IS NULL AND recovery_token IS NULL AND "
            "recovery_expires_at IS NULL) OR (recovery_owner IS NOT NULL AND "
            "recovery_token IS NOT NULL AND recovery_expires_at IS NOT NULL)",
            name="ck_reversion_attempts_recovery_bundle",
        ),
    )

    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("reversion_jobs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    worker_id: Mapped[str] = mapped_column(String(255), nullable=False)
    lease_token: Mapped[str] = mapped_column(String(36), nullable=False)
    leased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lease_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("reversion_broker_principals.principal_id", ondelete="RESTRICT"),
        nullable=False,
    )
    create_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    create_intent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unit_id: Mapped[str | None] = mapped_column(String(36))
    policy_revision: Mapped[str | None] = mapped_column(String(64))
    policy_specification: Mapped[str | None] = mapped_column(String(71))
    proof_id: Mapped[str | None] = mapped_column(String(36))
    proof_unit_id: Mapped[str | None] = mapped_column(String(36))
    proof_principal_id: Mapped[str | None] = mapped_column(String(36))
    proof_policy_revision: Mapped[str | None] = mapped_column(String(64))
    exit_evidence: Mapped[str | None] = mapped_column(String(71))
    empty_evidence: Mapped[str | None] = mapped_column(String(71))
    removal_evidence: Mapped[str | None] = mapped_column(String(71))
    proof_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    proof_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    reconciliation_ack_intent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    proof_recovery_token: Mapped[str | None] = mapped_column(String(36))
    recovery_owner: Mapped[str | None] = mapped_column(String(255))
    recovery_token: Mapped[str | None] = mapped_column(String(36))
    recovery_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class ReversionOrphanProofRow(Base):
    """Append-only proof receipt recovered without a matching restored attempt."""

    __tablename__ = "reversion_orphan_proofs"
    __table_args__ = (
        UniqueConstraint(
            "principal_id",
            "create_sequence",
            name="uq_reversion_orphan_principal_sequence",
        ),
        UniqueConstraint("proof_id", name="uq_reversion_orphan_proof"),
        UniqueConstraint("unit_id", name="uq_reversion_orphan_unit"),
        UniqueConstraint("attempt_id", name="uq_reversion_orphan_attempt"),
        CheckConstraint("create_sequence > 0", name="ck_reversion_orphan_sequence"),
    )

    principal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("reversion_broker_principals.principal_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    create_sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(36), nullable=False)
    unit_id: Mapped[str] = mapped_column(String(36), nullable=False)
    proof_id: Mapped[str] = mapped_column(String(36), nullable=False)
    policy_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_specification: Mapped[str] = mapped_column(String(71), nullable=False)
    exit_evidence: Mapped[str] = mapped_column(String(71), nullable=False)
    empty_evidence: Mapped[str] = mapped_column(String(71), nullable=False)
    removal_evidence: Mapped[str] = mapped_column(String(71), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ack_intent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index(
    "ix_reversion_jobs_queue",
    ReversionJobRow.state,
    ReversionJobRow.created_at,
    ReversionJobRow.id,
)
Index(
    "ix_reversion_jobs_owner_created",
    ReversionJobRow.owner_id,
    ReversionJobRow.created_at,
    ReversionJobRow.id,
)
Index(
    "ix_reversion_jobs_lease_expiry",
    ReversionJobRow.state,
    ReversionJobRow.lease_expires_at,
)
Index(
    "ix_reversion_jobs_cleanup",
    ReversionJobRow.state,
    ReversionJobRow.cleanup_completed,
    ReversionJobRow.cleanup_expires_at,
)
Index(
    "ix_reversion_attempts_recovery",
    ReversionAttemptRow.create_intent_at,
    ReversionAttemptRow.proof_recorded_at,
    ReversionAttemptRow.recovery_expires_at,
)
Index(
    "ix_reversion_attempts_pending_ack",
    ReversionAttemptRow.principal_id,
    ReversionAttemptRow.proof_acknowledged_at,
    ReversionAttemptRow.create_sequence,
)
Index(
    "ix_reversion_orphans_pending_ack",
    ReversionOrphanProofRow.principal_id,
    ReversionOrphanProofRow.acknowledged_at,
    ReversionOrphanProofRow.create_sequence,
)


class ComposerConnectionRow(Base):
    """Connection policy and public metadata; credential bytes live separately."""

    __tablename__ = "composer_connections"
    __table_args__ = (
        CheckConstraint(
            "scope IN ('instance', 'personal')", name="ck_composer_connection_scope"
        ),
        CheckConstraint(
            "identity_mode IN ('shared', 'individual')",
            name="ck_composer_identity_mode",
        ),
        CheckConstraint(
            "version > 0 AND generation > 0", name="ck_composer_connection_versions"
        ),
        CheckConstraint(
            "scope = 'instance' OR owner_id IS NOT NULL",
            name="ck_composer_personal_owner",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    identity_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(), nullable=False)
    selected_model: Mapped[str | None] = mapped_column(String())
    permitted_models: Mapped[str] = mapped_column(String(), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    outage: Mapped[bool] = mapped_column(Boolean, nullable=False)


class ComposerConnectionGrantRow(Base):
    """Explicit per-user permission to use one connection."""

    __tablename__ = "composer_connection_grants"

    connection_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("composer_connections.id", ondelete="CASCADE"),
        primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )


class ComposerCredentialRow(Base):
    """Opaque encrypted envelope values; no decrypted secret is persisted."""

    __tablename__ = "composer_credentials"
    __table_args__ = (
        UniqueConstraint(
            "connection_id", "user_id", name="uq_composer_credential_identity"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connection_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("composer_connections.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE")
    )
    api_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_certificate: Mapped[bytes | None] = mapped_column(LargeBinary)
    client_private_key: Mapped[bytes | None] = mapped_column(LargeBinary)
    ca_bundle: Mapped[bytes | None] = mapped_column(LargeBinary)
    outage: Mapped[bool] = mapped_column(Boolean, nullable=False)


Index(
    "ix_composer_credentials_identity",
    ComposerCredentialRow.connection_id,
    ComposerCredentialRow.user_id,
)
Index(
    "uq_composer_shared_credential",
    ComposerCredentialRow.connection_id,
    unique=True,
    sqlite_where=ComposerCredentialRow.user_id.is_(None),
    postgresql_where=ComposerCredentialRow.user_id.is_(None),
)


class ComposerPersonalPermissionRow(Base):
    """Explicit capability grant; absent users are denied by default."""

    __tablename__ = "composer_personal_permissions"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)


class ComposerPermissionAuditRow(Base):
    """Content-free append-only administrator permission change evidence."""

    __tablename__ = "composer_permission_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index(
    "ix_composer_permission_audit_user",
    ComposerPermissionAuditRow.user_id,
    ComposerPermissionAuditRow.created_at,
)


class ComposerConnectionAuditRow(Base):
    """Content-free immutable trail of connection, credential, and grant writes."""

    __tablename__ = "composer_connection_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    connection_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(36))
    target_user_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index(
    "ix_composer_connection_audit_retention",
    ComposerConnectionAuditRow.created_at,
    ComposerConnectionAuditRow.id,
)


class ComposerContentAuditRow(Base):
    """Immutable content-free evidence for Composer document mutations."""

    __tablename__ = "composer_content_audit"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    draft_id: Mapped[str | None] = mapped_column(String(36))
    draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index(
    "ix_composer_content_audit_retention",
    ComposerContentAuditRow.created_at,
    ComposerContentAuditRow.id,
)
Index(
    "ix_composer_content_audit_owner",
    ComposerContentAuditRow.owner_id,
    ComposerContentAuditRow.created_at,
    ComposerContentAuditRow.id,
)


class ComposerDraftRow(Base):
    """Owner-scoped mutable draft with a monotonic optimistic version."""

    __tablename__ = "composer_drafts"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_composer_draft_version"),
        CheckConstraint(
            "state IN ('active', 'deleting')", name="ck_composer_draft_state"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(), nullable=False)
    content: Mapped[str] = mapped_column(String(), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    current_revision_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index(
    "ix_composer_drafts_owner_updated",
    ComposerDraftRow.owner_id,
    ComposerDraftRow.updated_at,
)


class ComposerSourceRow(Base):
    """Scanned upload reservation, hidden until object integrity is verified."""

    __tablename__ = "composer_sources"
    __table_args__ = (
        CheckConstraint(
            "publication_state IN ('pending', 'published')",
            name="ck_composer_source_publication",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scan_receipt: Mapped[str] = mapped_column(String(), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    origin_job_id: Mapped[str | None] = mapped_column(String(36))
    origin_result_object_id: Mapped[str | None] = mapped_column(String(36))
    origin_result_sha256: Mapped[str | None] = mapped_column(String(64))
    publication_state: Mapped[str] = mapped_column(String(16), nullable=False)
    publication_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index(
    "ix_composer_sources_recovery",
    ComposerSourceRow.publication_state,
    ComposerSourceRow.lease_expires_at,
)


class ComposerMessageRow(Base):
    """Append-only conversation event for one draft."""

    __tablename__ = "composer_messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name="ck_composer_message_role"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    draft_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("composer_drafts.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(String(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index(
    "ix_composer_messages_history",
    ComposerMessageRow.draft_id,
    ComposerMessageRow.created_at,
    ComposerMessageRow.id,
)


class ComposerProposalRow(Base):
    """A model suggestion whose decision is protected by the draft version."""

    __tablename__ = "composer_proposals"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'accepted', 'edited', 'rejected')",
            name="ck_composer_proposal_state",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    draft_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("composer_drafts.id", ondelete="CASCADE"), nullable=False
    )
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    proposed_value: Mapped[str] = mapped_column(String(), nullable=False)
    decided_value: Mapped[str | None] = mapped_column(String())
    provenance: Mapped[str] = mapped_column(String(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(String(36))


Index(
    "ix_composer_proposals_history",
    ComposerProposalRow.draft_id,
    ComposerProposalRow.created_at,
    ComposerProposalRow.id,
)


class ComposerModelStepGateRow(Base):
    """One database lock serializes global model-call admission."""

    __tablename__ = "composer_model_step_gate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)


class ComposerModelStepRow(Base):
    """Durable, draft-linked model attempt without prompt or response bytes."""

    __tablename__ = "composer_model_steps"
    __table_args__ = (
        UniqueConstraint(
            "draft_id", "idempotency_key", name="uq_composer_step_idempotency"
        ),
        CheckConstraint(
            "state IN ('running', 'completed', 'cancelled', 'failed')",
            name="ck_composer_step_state",
        ),
        CheckConstraint("base_version > 0", name="ck_composer_step_base_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    draft_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("composer_drafts.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)
    connection_id: Mapped[str] = mapped_column(String(36), nullable=False)
    connection_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    approved_endpoint: Mapped[str] = mapped_column(String(), nullable=False)
    model: Mapped[str] = mapped_column(String(), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    proposal_id: Mapped[str | None] = mapped_column(String(36))
    safe_error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index(
    "ix_composer_model_steps_admission",
    ComposerModelStepRow.state,
    ComposerModelStepRow.expires_at,
)
Index(
    "ix_composer_model_steps_history",
    ComposerModelStepRow.draft_id,
    ComposerModelStepRow.created_at,
    ComposerModelStepRow.id,
)


class ComposerRevisionRow(Base):
    """Immutable revision snapshot; only publication lease fields can change."""

    __tablename__ = "composer_revisions"
    __table_args__ = (
        UniqueConstraint("draft_id", "number", name="uq_composer_revision_number"),
        UniqueConstraint(
            "draft_id", "idempotency_key", name="uq_composer_revision_idempotency"
        ),
        CheckConstraint("number > 0", name="ck_composer_revision_number"),
        CheckConstraint(
            "publication_state IN ('pending', 'published')",
            name="ck_composer_revision_publication",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    draft_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("composer_drafts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    expected_draft_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_reference: Mapped[str] = mapped_column(String(), nullable=False)
    template_reference: Mapped[str | None] = mapped_column(String())
    approved_values: Mapped[str] = mapped_column(String(), nullable=False)
    render_options: Mapped[str] = mapped_column(String(), nullable=False)
    model_identity: Mapped[str | None] = mapped_column(String())
    provenance: Mapped[str] = mapped_column(String(), nullable=False)
    operation: Mapped[str] = mapped_column(String(), nullable=False)
    restored_from_revision_id: Mapped[str | None] = mapped_column(String(36))
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    publication_state: Mapped[str] = mapped_column(String(16), nullable=False)
    publication_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


Index(
    "ix_composer_revisions_history",
    ComposerRevisionRow.draft_id,
    ComposerRevisionRow.number,
)
Index(
    "ix_composer_revisions_recovery",
    ComposerRevisionRow.publication_state,
    ComposerRevisionRow.lease_expires_at,
)


class ComposerArtifactRow(Base):
    """Exact content digest and immutable object key for a revision artifact."""

    __tablename__ = "composer_artifacts"
    __table_args__ = (
        UniqueConstraint("revision_id", "kind", name="uq_composer_artifact_kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    revision_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("composer_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
