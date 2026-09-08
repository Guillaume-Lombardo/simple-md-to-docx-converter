"""Add durable reverse broker reconciliation state.

Revision ID: 20260906_17
Revises: 20260906_16
Create Date: 2026-09-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260906_17"
down_revision: str | None = "20260906_16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("reversion_broker_principals") as batch:
        batch.drop_constraint(
            "ck_reversion_broker_principals_high_water", type_="check"
        )
        batch.create_check_constraint(
            "ck_reversion_broker_principals_high_water",
            "create_sequence_high_water >= 0 AND create_sequence_high_water <= 9223372036854775807",
        )
        batch.add_column(
            sa.Column(
                "reconciliation_complete",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("reconciliation_owner", sa.String(length=255), nullable=True)
        )
        batch.add_column(
            sa.Column("reconciliation_token", sa.String(length=36), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "reconciliation_expires_at", sa.DateTime(timezone=True), nullable=True
            )
        )
        batch.add_column(
            sa.Column(
                "reconciliation_cursor",
                sa.BigInteger(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(
            sa.Column("reconciliation_observed_head", sa.BigInteger(), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "reconciliation_fixed_point",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.create_check_constraint(
            "ck_reversion_broker_principals_cursor",
            "reconciliation_cursor >= 0 AND reconciliation_cursor <= 9223372036854775807",
        )
        batch.create_check_constraint(
            "ck_reversion_broker_principals_observed_head",
            "reconciliation_observed_head IS NULL OR (reconciliation_observed_head >= 0 AND reconciliation_observed_head <= 9223372036854775807)",
        )
        batch.create_check_constraint(
            "ck_reversion_broker_principals_cursor_high_water",
            "reconciliation_cursor <= create_sequence_high_water",
        )
        batch.create_check_constraint(
            "ck_reversion_broker_principals_observed_cursor",
            "reconciliation_observed_head IS NULL OR reconciliation_observed_head >= reconciliation_cursor",
        )
        batch.create_check_constraint(
            "ck_reversion_broker_principals_fixed_point",
            "NOT reconciliation_fixed_point OR reconciliation_observed_head IS NOT NULL",
        )
        batch.create_check_constraint(
            "ck_reversion_broker_principals_reconciliation_lease",
            "(reconciliation_owner IS NULL AND reconciliation_token IS NULL AND reconciliation_expires_at IS NULL) OR (reconciliation_owner IS NOT NULL AND reconciliation_token IS NOT NULL AND reconciliation_expires_at IS NOT NULL)",
        )
    with op.batch_alter_table("reversion_attempts") as batch:
        batch.add_column(
            sa.Column(
                "reconciliation_ack_intent_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
    op.create_table(
        "reversion_orphan_proofs",
        sa.Column("principal_id", sa.String(length=36), nullable=False),
        sa.Column("create_sequence", sa.BigInteger(), nullable=False),
        sa.Column("attempt_id", sa.String(length=36), nullable=False),
        sa.Column("unit_id", sa.String(length=36), nullable=False),
        sa.Column("proof_id", sa.String(length=36), nullable=False),
        sa.Column("policy_revision", sa.String(length=64), nullable=False),
        sa.Column("policy_specification", sa.String(length=71), nullable=False),
        sa.Column("exit_evidence", sa.String(length=71), nullable=False),
        sa.Column("empty_evidence", sa.String(length=71), nullable=False),
        sa.Column("removal_evidence", sa.String(length=71), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ack_intent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("create_sequence > 0", name="ck_reversion_orphan_sequence"),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["reversion_broker_principals.principal_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("principal_id", "create_sequence"),
        sa.UniqueConstraint(
            "principal_id",
            "create_sequence",
            name="uq_reversion_orphan_principal_sequence",
        ),
        sa.UniqueConstraint("proof_id", name="uq_reversion_orphan_proof"),
        sa.UniqueConstraint("unit_id", name="uq_reversion_orphan_unit"),
        sa.UniqueConstraint("attempt_id", name="uq_reversion_orphan_attempt"),
    )
    op.create_index(
        "ix_reversion_attempts_pending_ack",
        "reversion_attempts",
        ["principal_id", "proof_acknowledged_at", "create_sequence"],
    )
    op.create_index(
        "ix_reversion_orphans_pending_ack",
        "reversion_orphan_proofs",
        ["principal_id", "acknowledged_at", "create_sequence"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_reversion_orphans_pending_ack", table_name="reversion_orphan_proofs"
    )
    op.drop_index("ix_reversion_attempts_pending_ack", table_name="reversion_attempts")
    op.drop_table("reversion_orphan_proofs")
    with op.batch_alter_table("reversion_attempts") as batch:
        batch.drop_column("reconciliation_ack_intent_at")
    with op.batch_alter_table("reversion_broker_principals") as batch:
        batch.drop_constraint(
            "ck_reversion_broker_principals_reconciliation_lease", type_="check"
        )
        batch.drop_constraint(
            "ck_reversion_broker_principals_observed_head", type_="check"
        )
        batch.drop_constraint(
            "ck_reversion_broker_principals_fixed_point", type_="check"
        )
        batch.drop_constraint(
            "ck_reversion_broker_principals_observed_cursor", type_="check"
        )
        batch.drop_constraint(
            "ck_reversion_broker_principals_cursor_high_water", type_="check"
        )
        batch.drop_constraint("ck_reversion_broker_principals_cursor", type_="check")
        batch.drop_constraint(
            "ck_reversion_broker_principals_high_water", type_="check"
        )
        batch.create_check_constraint(
            "ck_reversion_broker_principals_high_water",
            "create_sequence_high_water >= 0",
        )
        batch.drop_column("reconciliation_fixed_point")
        batch.drop_column("reconciliation_observed_head")
        batch.drop_column("reconciliation_cursor")
        batch.drop_column("reconciliation_expires_at")
        batch.drop_column("reconciliation_token")
        batch.drop_column("reconciliation_owner")
        batch.drop_column("reconciliation_complete")
