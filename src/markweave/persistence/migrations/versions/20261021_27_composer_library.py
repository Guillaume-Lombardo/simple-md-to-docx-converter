"""Permissioned, cited Composer reference library and step citation references.

Revision ID: 20261021_27
Revises: 20260924_26
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20261021_27"
down_revision: str | None = "20260924_26"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _time(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    op.add_column(
        "composer_model_steps",
        sa.Column("library_refs", sa.String(), nullable=False, server_default="[]"),
    )
    op.create_table(
        "composer_library_documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("current_version_id", sa.String(36)),
        sa.Column("publication_state", sa.String(16), nullable=False),
        _time("created_at"),
        _time("updated_at"),
        sa.CheckConstraint("revision > 0", name="ck_composer_library_revision"),
        sa.CheckConstraint(
            "publication_state IN ('pending', 'published')",
            name="ck_composer_library_publication",
        ),
    )
    op.create_index(
        "ix_composer_library_documents_owner",
        "composer_library_documents",
        ["owner_id", "id"],
    )
    op.create_table(
        "composer_library_grants",
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("composer_library_documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        _time("created_at"),
    )
    op.create_index(
        "ix_composer_library_grants_user", "composer_library_grants", ["user_id"]
    )
    op.create_table(
        "composer_library_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "document_id",
            sa.String(36),
            sa.ForeignKey("composer_library_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("object_owner_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("scan_receipt", sa.String(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("publication_state", sa.String(16), nullable=False),
        sa.Column("publication_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        _time("created_at"),
        sa.UniqueConstraint(
            "document_id", "number", name="uq_composer_library_versions_number"
        ),
        sa.CheckConstraint("number > 0", name="ck_composer_library_versions_number"),
        sa.CheckConstraint("size > 0", name="ck_composer_library_versions_size"),
        sa.CheckConstraint(
            "chunk_count >= 0", name="ck_composer_library_versions_chunks"
        ),
        sa.CheckConstraint(
            "publication_state IN ('pending', 'published')",
            name="ck_composer_library_versions_publication",
        ),
    )
    op.create_index(
        "ix_composer_library_versions_document",
        "composer_library_versions",
        ["document_id"],
    )
    op.create_index(
        "ix_composer_library_versions_recovery",
        "composer_library_versions",
        ["publication_state", "lease_expires_at"],
    )
    op.create_table(
        "composer_library_chunks",
        sa.Column(
            "version_id",
            sa.String(36),
            sa.ForeignKey("composer_library_versions.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("text", sa.String(), nullable=False),
        sa.CheckConstraint("ordinal >= 0", name="ck_composer_library_chunks_ordinal"),
    )


def downgrade() -> None:
    op.drop_table("composer_library_chunks")
    op.drop_index(
        "ix_composer_library_versions_recovery",
        table_name="composer_library_versions",
    )
    op.drop_index(
        "ix_composer_library_versions_document",
        table_name="composer_library_versions",
    )
    op.drop_table("composer_library_versions")
    op.drop_index(
        "ix_composer_library_grants_user", table_name="composer_library_grants"
    )
    op.drop_table("composer_library_grants")
    op.drop_index(
        "ix_composer_library_documents_owner", table_name="composer_library_documents"
    )
    op.drop_table("composer_library_documents")
    with op.batch_alter_table("composer_model_steps") as batch:
        batch.drop_column("library_refs")
