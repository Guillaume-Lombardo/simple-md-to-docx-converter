"""Representative records for databases migrated to an earlier recovery-set revision.

The schema itself always comes from the repository's Alembic migrations; this
helper only inserts rows through tables reflected from that migrated schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Connection, Engine, MetaData, Table, insert

# The last schema before T91 added `composer_fill_plans` in migration 20260924_25.
PRE_FILL_PLAN_REVISION = "20260923_24"


@dataclass(frozen=True, slots=True)
class OlderSchemaRecords:
    """Identifiers and stable objects referenced by the inserted rows."""

    owner_id: str
    job_id: str
    draft_id: str
    revision_id: str
    objects: dict[str, bytes]

    def key(self, scope: str) -> str:
        return next(key for key in self.objects if key.startswith(f"{scope}/"))


def _table(connection: Connection, name: str) -> Table:
    return Table(name, MetaData(), autoload_with=connection)


def populate_older_schema(engine: Engine) -> OlderSchemaRecords:
    """Insert one user with a template, a conversion job, and a Composer revision."""

    owner = str(uuid4())
    template_id, version_id = str(uuid4()), str(uuid4())
    job_id, upload_id, result_id = str(uuid4()), str(uuid4()), str(uuid4())
    source_id, draft_id = str(uuid4()), str(uuid4())
    revision_id, artifact_id = str(uuid4()), str(uuid4())
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    source_reference = f'{{"object_id": "{source_id}"}}'
    with engine.begin() as connection:
        connection.execute(
            insert(_table(connection, "users")),
            {
                "id": owner,
                "username": "Older schema owner",
                "normalized_username": f"older-{uuid4().hex}",
                "password_hash": "hash:older",
                "role": "user",
                "active": True,
                "auth_version": 0,
                "password_change_required": False,
            },
        )
        connection.execute(
            insert(_table(connection, "templates")),
            {
                "id": template_id,
                "owner_id": owner,
                "name": "Older template",
                "normalized_name": "older template",
                "description": "",
                "normalized_description": "",
                "status": "active",
                "revision": 1,
                "publication_state": "published",
                "kind": "docx",
            },
        )
        connection.execute(
            insert(_table(connection, "template_versions")),
            {
                "id": version_id,
                "template_id": template_id,
                "version_number": 1,
                "object_owner_id": owner,
                "sha256": "a" * 64,
                "size": 8,
                "created_at": now,
                "created_by": owner,
                "declared_fonts": "[]",
                "resolved_fonts": "[]",
                "validation_trace": "{}",
                "publication_state": "published",
            },
        )
        connection.execute(
            insert(_table(connection, "conversion_jobs")),
            {
                "id": job_id,
                "owner_id": owner,
                "source_object_id": upload_id,
                "output": "docx",
                "component_versions": "{}",
                "state": "succeeded",
                "step": "complete",
                "progress": 100,
                "request_digest": "b" * 64,
                "created_at": now,
                "updated_at": now,
                "attempt": 1,
                "source_ready": True,
                "cancel_requested": False,
                "cleanup_completed": False,
                "result_object_id": result_id,
            },
        )
        connection.execute(
            insert(_table(connection, "composer_sources")),
            {
                "id": source_id,
                "owner_id": owner,
                "sha256": "c" * 64,
                "size": 6,
                "scan_receipt": "scan-ok",
                "media_type": "text/markdown",
                "publication_state": "published",
                "created_at": now,
                "kind": "upload",
            },
        )
        connection.execute(
            insert(_table(connection, "composer_drafts")),
            {
                "id": draft_id,
                "owner_id": owner,
                "title": "Older draft",
                "source_reference": source_reference,
                "content": "# Older draft",
                "state": "active",
                "version": 1,
                "current_revision_id": revision_id,
                "created_at": now,
                "updated_at": now,
            },
        )
        connection.execute(
            insert(_table(connection, "composer_revisions")),
            {
                "id": revision_id,
                "draft_id": draft_id,
                "number": 1,
                "actor_id": owner,
                "expected_draft_version": 1,
                "source_reference": source_reference,
                "approved_values": "{}",
                "render_options": "{}",
                "provenance": "human",
                "operation": "generate",
                "idempotency_key": "older-schema",
                "request_digest": "d" * 64,
                "publication_state": "published",
                "created_at": now,
            },
        )
        connection.execute(
            insert(_table(connection, "composer_artifacts")),
            {
                "id": artifact_id,
                "revision_id": revision_id,
                "kind": "download",
                "sha256": "e" * 64,
                "size": 8,
                "media_type": "text/plain",
            },
        )
    return OlderSchemaRecords(
        owner,
        job_id,
        draft_id,
        revision_id,
        {
            f"template-versions/{owner}/{version_id}": b"template",
            f"uploads/{owner}/{upload_id}": b"# source",
            f"results/{owner}/{result_id}": b"result",
            f"composer-sources/{owner}/{source_id}": b"source",
            f"composer-artifacts/{owner}/{artifact_id}": b"download",
        },
    )
