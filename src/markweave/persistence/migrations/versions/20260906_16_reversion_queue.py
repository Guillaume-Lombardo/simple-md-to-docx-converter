"""Add the durable reverse-conversion queue and attempt ledger.

Revision ID: 20260906_16
Revises: 20260901_15
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_16"
down_revision: str | None = "20260901_15"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reversion_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=False),
        sa.Column("source_object_id", sa.String(length=36), nullable=False),
        sa.Column("source_stem", sa.String(length=255), nullable=False),
        sa.Column("source_extension", sa.String(length=16), nullable=False),
        sa.Column("source_family", sa.String(length=32), nullable=False),
        sa.Column("detected_format", sa.String(length=32), nullable=True),
        sa.Column("parser_format", sa.String(length=32), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_size", sa.BigInteger(), nullable=False),
        sa.Column("component_versions", sa.String(), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_digest", sa.String(length=64), nullable=True),
        sa.Column("correlation_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("step", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("source_ready", sa.Boolean(), nullable=False),
        sa.Column("lease_owner", sa.String(length=255), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("result_mode", sa.String(length=48), nullable=True),
        sa.Column("result_object_id", sa.String(length=36), nullable=True),
        sa.Column("result_sha256", sa.String(length=64), nullable=True),
        sa.Column("result_size", sa.BigInteger(), nullable=True),
        sa.Column("trace_metadata", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleanup_completed", sa.Boolean(), nullable=False),
        sa.Column("cleanup_owner", sa.String(length=255), nullable=True),
        sa.Column("cleanup_token", sa.String(length=36), nullable=True),
        sa.Column("cleanup_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued','running','succeeded','failed','cancelled','expired')",
            name="ck_reversion_jobs_state",
        ),
        sa.CheckConstraint(
            "step IN ('queued','isolating','converting','validating','publishing','complete')",
            name="ck_reversion_jobs_step",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_reversion_jobs_attempt"),
        sa.CheckConstraint("source_size > 0", name="ck_reversion_jobs_source_size"),
        sa.CheckConstraint(
            "source_family IN ('word','powerpoint','excel','opendocument','rtf','epub','csv','pdf')",
            name="ck_reversion_jobs_source_family",
        ),
        sa.CheckConstraint(
            "(state = 'running' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL AND "
            "current_attempt_id IS NOT NULL) OR (state <> 'running' AND lease_owner IS "
            "NULL AND lease_token IS NULL AND lease_expires_at IS NULL AND heartbeat_at "
            "IS NULL AND current_attempt_id IS NULL)",
            name="ck_reversion_jobs_lease_bundle",
        ),
        sa.CheckConstraint(
            "(state = 'succeeded' AND result_mode IS NOT NULL AND result_object_id IS "
            "NOT NULL AND result_sha256 IS NOT NULL AND result_size > 0 AND trace_metadata "
            "IS NOT NULL) OR (state <> 'succeeded' AND result_mode IS NULL AND "
            "result_object_id IS NULL AND result_sha256 IS NULL AND result_size IS NULL "
            "AND trace_metadata IS NULL)",
            name="ck_reversion_jobs_result_bundle",
        ),
        sa.CheckConstraint(
            "(state = 'failed' AND error_code IS NOT NULL AND error_message IS NOT NULL) "
            "OR (state <> 'failed' AND error_code IS NULL AND error_message IS NULL)",
            name="ck_reversion_jobs_error_bundle",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("current_attempt_id"),
        sa.UniqueConstraint(
            "owner_id", "idempotency_digest", name="uq_reversion_jobs_owner_idempotency"
        ),
    )
    op.create_table(
        "reversion_broker_principals",
        sa.Column("principal_id", sa.String(length=36), nullable=False),
        sa.Column("create_sequence_high_water", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "create_sequence_high_water >= 0",
            name="ck_reversion_broker_principals_high_water",
        ),
        sa.PrimaryKeyConstraint("principal_id"),
    )
    op.create_table(
        "reversion_attempts",
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(length=255), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=False),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("principal_id", sa.String(length=36), nullable=False),
        sa.Column("create_sequence", sa.BigInteger(), nullable=False),
        sa.Column("create_intent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unit_id", sa.String(length=36), nullable=True),
        sa.Column("policy_revision", sa.String(length=64), nullable=True),
        sa.Column("policy_specification", sa.String(length=71), nullable=True),
        sa.Column("proof_id", sa.String(length=36), nullable=True),
        sa.Column("proof_unit_id", sa.String(length=36), nullable=True),
        sa.Column("proof_principal_id", sa.String(length=36), nullable=True),
        sa.Column("proof_policy_revision", sa.String(length=64), nullable=True),
        sa.Column("exit_evidence", sa.String(length=71), nullable=True),
        sa.Column("empty_evidence", sa.String(length=71), nullable=True),
        sa.Column("removal_evidence", sa.String(length=71), nullable=True),
        sa.Column("proof_recorded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("proof_acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_owner", sa.String(length=255), nullable=True),
        sa.Column("recovery_token", sa.String(length=36), nullable=True),
        sa.Column("recovery_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_number > 0", name="ck_reversion_attempts_number"),
        sa.CheckConstraint(
            "create_sequence > 0", name="ck_reversion_attempts_sequence"
        ),
        sa.CheckConstraint(
            "(create_intent_at IS NULL AND policy_revision IS NULL AND "
            "policy_specification IS NULL AND unit_id IS NULL) OR (create_intent_at IS "
            "NOT NULL AND policy_revision IS NOT NULL AND policy_specification IS NOT NULL)",
            name="ck_reversion_attempts_create_intent",
        ),
        sa.CheckConstraint(
            "(proof_id IS NULL AND proof_unit_id IS NULL AND proof_principal_id IS NULL "
            "AND proof_policy_revision IS NULL AND exit_evidence IS NULL AND empty_evidence "
            "IS NULL AND removal_evidence IS NULL AND proof_recorded_at IS NULL AND "
            "proof_acknowledged_at IS NULL) OR (proof_id IS NOT NULL AND proof_unit_id IS "
            "NOT NULL AND proof_principal_id IS NOT NULL AND proof_policy_revision IS NOT "
            "NULL AND exit_evidence IS NOT NULL AND empty_evidence IS NOT NULL AND "
            "removal_evidence IS NOT NULL AND proof_recorded_at IS NOT NULL)",
            name="ck_reversion_attempts_proof_bundle",
        ),
        sa.CheckConstraint(
            "(recovery_owner IS NULL AND recovery_token IS NULL AND recovery_expires_at IS "
            "NULL) OR (recovery_owner IS NOT NULL AND recovery_token IS NOT NULL AND "
            "recovery_expires_at IS NOT NULL)",
            name="ck_reversion_attempts_recovery_bundle",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["reversion_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["reversion_broker_principals.principal_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("attempt_id"),
        sa.UniqueConstraint(
            "job_id", "attempt_number", name="uq_reversion_attempts_job_number"
        ),
        sa.UniqueConstraint(
            "principal_id",
            "create_sequence",
            name="uq_reversion_attempts_principal_sequence",
        ),
        sa.UniqueConstraint("unit_id", name="uq_reversion_attempts_unit"),
        sa.UniqueConstraint("proof_id", name="uq_reversion_attempts_proof"),
    )
    op.create_index(
        "ix_reversion_jobs_queue", "reversion_jobs", ["state", "created_at", "id"]
    )
    op.create_index(
        "ix_reversion_jobs_owner_created",
        "reversion_jobs",
        ["owner_id", "created_at", "id"],
    )
    op.create_index(
        "ix_reversion_jobs_lease_expiry",
        "reversion_jobs",
        ["state", "lease_expires_at"],
    )
    op.create_index(
        "ix_reversion_jobs_cleanup",
        "reversion_jobs",
        ["state", "cleanup_completed", "cleanup_expires_at"],
    )
    op.create_index(
        "ix_reversion_attempts_recovery",
        "reversion_attempts",
        ["create_intent_at", "proof_recorded_at", "recovery_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_reversion_attempts_recovery", table_name="reversion_attempts")
    op.drop_index("ix_reversion_jobs_cleanup", table_name="reversion_jobs")
    op.drop_index("ix_reversion_jobs_lease_expiry", table_name="reversion_jobs")
    op.drop_index("ix_reversion_jobs_owner_created", table_name="reversion_jobs")
    op.drop_index("ix_reversion_jobs_queue", table_name="reversion_jobs")
    op.drop_table("reversion_attempts")
    op.drop_table("reversion_broker_principals")
    op.drop_table("reversion_jobs")
