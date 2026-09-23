"""Persist Composer connections, drafts, conversation, and revisions.

Revision ID: 20260923_20
Revises: 20260921_19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260923_20"
down_revision: str | None = "20260921_19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REVISION_IMMUTABLE_COLUMNS = (
    "id",
    "draft_id",
    "number",
    "actor_id",
    "expected_draft_version",
    "source_reference",
    "template_reference",
    "approved_values",
    "render_options",
    "model_identity",
    "provenance",
    "operation",
    "restored_from_revision_id",
    "idempotency_key",
    "request_digest",
    "created_at",
)
_IMMUTABLE_UPDATE_TABLES = (
    "composer_artifacts",
    "composer_messages",
    "composer_permission_audit",
    "composer_connection_audit",
    "composer_content_audit",
)
_GUARDED_AUDIT_TABLES = (
    "composer_connection_audit",
    "composer_permission_audit",
    "composer_content_audit",
)


def upgrade() -> None:
    """Add private Composer records and publication bookkeeping."""

    op.create_table(
        "composer_connections",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column(
            "owner_id", sa.String(36), sa.ForeignKey("users.id", ondelete="RESTRICT")
        ),
        sa.Column("identity_mode", sa.String(16), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("endpoint", sa.String(), nullable=False),
        sa.Column("selected_model", sa.String()),
        sa.Column("permitted_models", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("outage", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "scope IN ('instance', 'personal')", name="ck_composer_connection_scope"
        ),
        sa.CheckConstraint(
            "identity_mode IN ('shared', 'individual')",
            name="ck_composer_identity_mode",
        ),
        sa.CheckConstraint(
            "version > 0 AND generation > 0", name="ck_composer_connection_versions"
        ),
        sa.CheckConstraint(
            "scope = 'instance' OR owner_id IS NOT NULL",
            name="ck_composer_personal_owner",
        ),
    )
    op.create_table(
        "composer_connection_grants",
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("composer_connections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "composer_credentials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "connection_id",
            sa.String(36),
            sa.ForeignKey("composer_connections.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE")
        ),
        sa.Column("api_key", sa.LargeBinary()),
        sa.Column("client_certificate", sa.LargeBinary()),
        sa.Column("client_private_key", sa.LargeBinary()),
        sa.Column("ca_bundle", sa.LargeBinary()),
        sa.Column("outage", sa.Boolean(), nullable=False),
        sa.UniqueConstraint(
            "connection_id", "user_id", name="uq_composer_credential_identity"
        ),
    )
    op.create_index(
        "ix_composer_credentials_identity",
        "composer_credentials",
        ["connection_id", "user_id"],
    )
    op.create_index(
        "uq_composer_shared_credential",
        "composer_credentials",
        ["connection_id"],
        unique=True,
        sqlite_where=sa.text("user_id IS NULL"),
        postgresql_where=sa.text("user_id IS NULL"),
    )
    op.create_table(
        "composer_personal_permissions",
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
    )
    op.create_table(
        "composer_permission_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_composer_permission_audit_user",
        "composer_permission_audit",
        ["user_id", "created_at"],
    )
    op.create_table(
        "composer_connection_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("connection_id", sa.String(36), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.String(36)),
        sa.Column("target_user_id", sa.String(36)),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_composer_connection_audit_retention",
        "composer_connection_audit",
        ["created_at", "id"],
    )
    op.create_table(
        "composer_content_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(36), nullable=False),
        sa.Column("draft_id", sa.String(36)),
        sa.Column("draft_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_composer_content_audit_retention",
        "composer_content_audit",
        ["created_at", "id"],
    )
    op.create_index(
        "ix_composer_content_audit_owner",
        "composer_content_audit",
        ["owner_id", "created_at", "id"],
    )
    op.create_table(
        "composer_drafts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("source_reference", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("current_revision_id", sa.String(36)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_composer_draft_version"),
        sa.CheckConstraint(
            "state IN ('active', 'deleting')", name="ck_composer_draft_state"
        ),
    )
    op.create_index(
        "ix_composer_drafts_owner_updated",
        "composer_drafts",
        ["owner_id", "updated_at"],
    )
    op.create_table(
        "composer_sources",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("scan_receipt", sa.String(), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("origin_job_id", sa.String(36)),
        sa.Column("origin_result_object_id", sa.String(36)),
        sa.Column("origin_result_sha256", sa.String(64)),
        sa.Column("publication_state", sa.String(16), nullable=False),
        sa.Column("publication_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "publication_state IN ('pending', 'published')",
            name="ck_composer_source_publication",
        ),
    )
    op.create_index(
        "ix_composer_sources_recovery",
        "composer_sources",
        ["publication_state", "lease_expires_at"],
    )
    op.create_table(
        "composer_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "draft_id",
            sa.String(36),
            sa.ForeignKey("composer_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "role IN ('user', 'assistant', 'system')", name="ck_composer_message_role"
        ),
    )
    op.create_index(
        "ix_composer_messages_history",
        "composer_messages",
        ["draft_id", "created_at", "id"],
    )
    op.create_table(
        "composer_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "draft_id",
            sa.String(36),
            sa.ForeignKey("composer_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("proposed_value", sa.String(), nullable=False),
        sa.Column("decided_value", sa.String()),
        sa.Column("provenance", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.String(36)),
        sa.CheckConstraint(
            "state IN ('pending', 'accepted', 'edited', 'rejected')",
            name="ck_composer_proposal_state",
        ),
    )
    op.create_index(
        "ix_composer_proposals_history",
        "composer_proposals",
        ["draft_id", "created_at", "id"],
    )
    op.create_table(
        "composer_model_step_gate",
        sa.Column("id", sa.Integer(), primary_key=True),
    )
    op.execute("INSERT INTO composer_model_step_gate (id) VALUES (1)")
    op.create_table(
        "composer_model_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "draft_id",
            sa.String(36),
            sa.ForeignKey("composer_drafts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("owner_id", sa.String(36), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.String(36), nullable=False),
        sa.Column("connection_generation", sa.Integer(), nullable=False),
        sa.Column("approved_endpoint", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("proposal_id", sa.String(36)),
        sa.Column("safe_error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "draft_id", "idempotency_key", name="uq_composer_step_idempotency"
        ),
        sa.CheckConstraint(
            "state IN ('running', 'completed', 'cancelled', 'failed')",
            name="ck_composer_step_state",
        ),
        sa.CheckConstraint("base_version > 0", name="ck_composer_step_base_version"),
    )
    op.create_index(
        "ix_composer_model_steps_admission",
        "composer_model_steps",
        ["state", "expires_at"],
    )
    op.create_index(
        "ix_composer_model_steps_history",
        "composer_model_steps",
        ["draft_id", "created_at", "id"],
    )
    op.create_table(
        "composer_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "draft_id",
            sa.String(36),
            sa.ForeignKey("composer_drafts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=False),
        sa.Column("expected_draft_version", sa.Integer(), nullable=False),
        sa.Column("source_reference", sa.String(), nullable=False),
        sa.Column("template_reference", sa.String()),
        sa.Column("approved_values", sa.String(), nullable=False),
        sa.Column("render_options", sa.String(), nullable=False),
        sa.Column("model_identity", sa.String()),
        sa.Column("provenance", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("restored_from_revision_id", sa.String(36)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("publication_state", sa.String(16), nullable=False),
        sa.Column("publication_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("draft_id", "number", name="uq_composer_revision_number"),
        sa.UniqueConstraint(
            "draft_id", "idempotency_key", name="uq_composer_revision_idempotency"
        ),
        sa.CheckConstraint("number > 0", name="ck_composer_revision_number"),
        sa.CheckConstraint(
            "publication_state IN ('pending', 'published')",
            name="ck_composer_revision_publication",
        ),
    )
    op.create_index(
        "ix_composer_revisions_history", "composer_revisions", ["draft_id", "number"]
    )
    op.create_index(
        "ix_composer_revisions_recovery",
        "composer_revisions",
        ["publication_state", "lease_expires_at"],
    )
    op.create_table(
        "composer_artifacts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "revision_id",
            sa.String(36),
            sa.ForeignKey("composer_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.UniqueConstraint("revision_id", "kind", name="uq_composer_artifact_kind"),
    )
    _protect_immutable_rows()


def _protect_immutable_rows() -> None:
    if op.get_bind().dialect.name == "sqlite":
        condition = " OR ".join(
            f"NEW.{column} IS NOT OLD.{column}"
            for column in _REVISION_IMMUTABLE_COLUMNS
        )
        op.execute(
            "CREATE TRIGGER composer_revisions_immutable_update "
            "BEFORE UPDATE ON composer_revisions WHEN "
            + condition
            + " OR (OLD.publication_state = 'published' AND ("
            "NEW.publication_state IS NOT OLD.publication_state OR "
            "NEW.publication_token IS NOT OLD.publication_token OR "
            "NEW.lease_expires_at IS NOT OLD.lease_expires_at)) "
            "BEGIN SELECT RAISE(ABORT, 'immutable Composer revision'); END"
        )
        for table in _IMMUTABLE_UPDATE_TABLES:
            op.execute(
                f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
                "BEGIN SELECT RAISE(ABORT, 'immutable Composer record'); END"
            )
        for table in _GUARDED_AUDIT_TABLES:
            op.execute(
                f"CREATE TRIGGER {table}_immutable_delete "  # noqa: S608 - fixed migration table identifiers
                f"BEFORE DELETE ON {table} "
                "WHEN NOT EXISTS (SELECT 1 FROM audit_cleanup_guards) "
                "BEGIN SELECT RAISE(ABORT, 'immutable Composer audit'); END"
            )
        return
    before = ", ".join(f"NEW.{column}" for column in _REVISION_IMMUTABLE_COLUMNS)
    after = ", ".join(f"OLD.{column}" for column in _REVISION_IMMUTABLE_COLUMNS)
    op.execute(
        "CREATE FUNCTION reject_composer_revision_mutation() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN IF ROW("
        + before
        + ") IS DISTINCT FROM ROW("
        + after
        + ") OR (OLD.publication_state = 'published' AND "
        "ROW(NEW.publication_state, NEW.publication_token, NEW.lease_expires_at) "
        "IS DISTINCT FROM ROW(OLD.publication_state, OLD.publication_token, OLD.lease_expires_at)) "
        "THEN RAISE EXCEPTION 'immutable Composer revision' USING ERRCODE = '23000'; "
        "END IF; RETURN NEW; END $$"
    )
    op.execute(
        "CREATE TRIGGER composer_revisions_immutable_update BEFORE UPDATE ON composer_revisions "
        "FOR EACH ROW EXECUTE FUNCTION reject_composer_revision_mutation()"
    )
    op.execute(
        "CREATE FUNCTION reject_composer_record_mutation() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable Composer record' "
        "USING ERRCODE = '23000'; END $$"
    )
    for table in _IMMUTABLE_UPDATE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable_update BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_composer_record_mutation()"
        )
    for table in _GUARDED_AUDIT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable_delete "
            f"BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_unauthorized_audit_delete()"
        )


def downgrade() -> None:
    """Remove Composer records in dependency order."""

    if op.get_bind().dialect.name == "sqlite":
        op.execute("DROP TRIGGER IF EXISTS composer_revisions_immutable_update")
        for table in _GUARDED_AUDIT_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_delete")
        for table in _IMMUTABLE_UPDATE_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_update")
    else:
        op.execute(
            "DROP TRIGGER IF EXISTS composer_revisions_immutable_update ON composer_revisions"
        )
        for table in _GUARDED_AUDIT_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_delete ON {table}")
        for table in _IMMUTABLE_UPDATE_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_update ON {table}")
        op.execute("DROP FUNCTION reject_composer_revision_mutation()")
        op.execute("DROP FUNCTION reject_composer_record_mutation()")
    op.drop_table("composer_artifacts")
    op.drop_index("ix_composer_revisions_recovery", table_name="composer_revisions")
    op.drop_index("ix_composer_revisions_history", table_name="composer_revisions")
    op.drop_table("composer_revisions")
    op.drop_index("ix_composer_proposals_history", table_name="composer_proposals")
    op.drop_table("composer_proposals")
    op.drop_index("ix_composer_model_steps_history", table_name="composer_model_steps")
    op.drop_index(
        "ix_composer_model_steps_admission", table_name="composer_model_steps"
    )
    op.drop_table("composer_model_steps")
    op.drop_table("composer_model_step_gate")
    op.drop_index("ix_composer_messages_history", table_name="composer_messages")
    op.drop_table("composer_messages")
    op.drop_index("ix_composer_sources_recovery", table_name="composer_sources")
    op.drop_table("composer_sources")
    op.drop_index("ix_composer_drafts_owner_updated", table_name="composer_drafts")
    op.drop_table("composer_drafts")
    op.drop_index("ix_composer_credentials_identity", table_name="composer_credentials")
    op.drop_index("uq_composer_shared_credential", table_name="composer_credentials")
    op.drop_table("composer_credentials")
    op.drop_index(
        "ix_composer_permission_audit_user", table_name="composer_permission_audit"
    )
    op.drop_table("composer_permission_audit")
    op.drop_index(
        "ix_composer_content_audit_owner", table_name="composer_content_audit"
    )
    op.drop_index(
        "ix_composer_content_audit_retention", table_name="composer_content_audit"
    )
    op.drop_table("composer_content_audit")
    op.drop_index(
        "ix_composer_connection_audit_retention", table_name="composer_connection_audit"
    )
    op.drop_table("composer_connection_audit")
    op.drop_table("composer_personal_permissions")
    op.drop_table("composer_connection_grants")
    op.drop_table("composer_connections")
