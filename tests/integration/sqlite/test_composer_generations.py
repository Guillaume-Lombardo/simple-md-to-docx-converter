"""Composer generation links stay durable while conversion jobs run separately."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from time import sleep
from types import SimpleNamespace
from uuid import UUID
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.composer.drafts import ProposalState
from markweave.composer.revisions import ArtifactContent, RevisionSnapshot
from markweave.config import Settings
from markweave.http.routers import composer_generations as generation_routes
from markweave.jobs.errors import JobConflictError
from markweave.jobs.models import JobOutput, JobState, SourceKind
from markweave.persistence.composer import SqlComposerRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import UserRow
from markweave.persistence.sql import create_database_engine
from markweave.presentations.models import PresentationDialect, PresentationOptions
from markweave.storage import FilesystemObjectStore
from tests.settings import template_settings

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def _client(tmp_path: Path, mocker: MockerFixture):
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    store = SqlComposerRepository(engine, FilesystemObjectStore(tmp_path))
    owner = User(UUID(int=1), "owner", "owner", "hash", Role.USER)
    with Session(engine) as database, database.begin():
        database.add(
            UserRow(
                id=str(owner.id),
                username=owner.username,
                normalized_username=owner.normalized_username,
                password_hash="hash",  # noqa: S106 - isolated fixture
                role=owner.role.value,
                active=True,
                auth_version=0,
                password_change_required=False,
            )
        )
    authentication = mocker.Mock(spec=AuthenticationService)
    authentication.bootstrap_admin.return_value = owner
    authentication.authenticate.return_value = owner
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-password",  # noqa: S106 - isolated fixture
        storage_profile="standalone",
        standalone_data_directory=str(tmp_path),
        conversion_upload_max_bytes=100_000,
        conversion_request_max_bytes=110_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3600,
        composer_upload_max_bytes=100_000,
        composer_http_request_max_bytes=4096,
    )
    jobs = mocker.Mock()
    scanner = mocker.Mock()
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=mocker.Mock(),
            jobs=jobs,
            scanner=scanner,
            composer_store=store,
        ),
    )
    return (
        engine,
        store,
        owner,
        jobs,
        scanner,
        TestClient(app, base_url="https://testserver"),
    )


def _source(archive: bool) -> bytes:
    if not archive:
        return b"# Original\n"
    image = BytesIO()
    Image.new("RGB", (1, 1), (255, 0, 0)).save(image, format="PNG")
    output = BytesIO()
    with ZipFile(output, "w") as package:
        package.writestr("document.md", "# Original\n![pixel](assets/pixel.png)\n")
        package.writestr("assets/pixel.png", image.getvalue())
    return output.getvalue()


def _fake_submit(job, request, _key):
    job.source_sha256 = hashlib.sha256(request.source).hexdigest()
    job.source_kind = request.source_kind
    job.source_filename = request.source_filename
    job.component_versions = request.component_versions
    job.presentation_options = request.presentation_options
    return job, False


@pytest.mark.parametrize("output", ["docx", "pdf", "pptx"])
@pytest.mark.parametrize("source_kind", ["markdown", "zip", "reversion_zip"])
def test_generation_queues_exact_approved_source_and_publishes_native_result(  # noqa: PLR0915 - complete durable workflow
    tmp_path: Path, mocker: MockerFixture, output: str, source_kind: str
) -> None:
    engine, store, owner, jobs, scanner, client = _client(tmp_path, mocker)
    try:
        archive = source_kind != "markdown"
        source = _source(archive)
        if source_kind == "reversion_zip":
            reverse = BytesIO(source)
            with ZipFile(reverse, "a") as package:
                package.writestr("manifest.json", b"{}")
            source = reverse.getvalue()
        draft = store.create_draft_with_source(
            owner.id,
            source,
            "scanner-approved",
            title="Draft",
            content="# Original\n![pixel](assets/pixel.png)\n"
            if archive
            else "# Approved\n",
            media_type="application/zip" if archive else "text/markdown",
            source_kind="reversion_result" if source_kind == "reversion_zip" else None,
            origin_job_id=UUID(int=201) if source_kind == "reversion_zip" else None,
            origin_result_object_id=UUID(int=202)
            if source_kind == "reversion_zip"
            else None,
            origin_result_sha256=hashlib.sha256(source).hexdigest()
            if source_kind == "reversion_zip"
            else None,
        )
        base = f"/api/v1/composer/drafts/{draft.id}"
        result = (
            b"%PDF-1.7\nexact result" if output == "pdf" else b"PK\x03\x04exact result"
        )
        job = SimpleNamespace(
            id=UUID(int=100),
            owner_id=owner.id,
            source_sha256="",
            output=JobOutput(output),
            state=JobState.QUEUED,
            template_id=None,
            template_version_id=None,
        )

        jobs.submit.side_effect = lambda request, key: _fake_submit(job, request, key)
        jobs.get_visible.return_value = job
        jobs.download.return_value = (job, result)
        jobs.download_manifest.return_value = (job, b'{"trace":"exact"}')
        with client:
            if archive:
                proposal = store.create_proposal(
                    owner.id,
                    draft.id,
                    base_version=draft.version,
                    proposed_value="# Approved\n![pixel](assets/pixel.png)\n",
                    provenance="reviewed-suggestion",
                )
                store.decide_proposal(
                    owner.id,
                    draft.id,
                    proposal.id,
                    if_match=store.get_draft(owner.id, draft.id).etag,
                    state=ProposalState.ACCEPTED,
                    decided_value=None,
                )
                markdown_revision = client.post(
                    f"{base}/proposals/{proposal.id}/publish",
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": store.get_draft(owner.id, draft.id).etag,
                        "Idempotency-Key": "approved-1",
                    },
                )
                assert store.get_draft(owner.id, draft.id).content.startswith(
                    "# Approved"
                )
            else:
                markdown_revision = client.post(
                    f"{base}/revisions/from-draft",
                    headers={
                        "X-CSRF-Token": "csrf",
                        "If-Match": draft.etag,
                        "Idempotency-Key": "approved-1",
                    },
                )
            assert markdown_revision.status_code == 201
            source_revision_id = markdown_revision.json()["id"]
            draft_etag = markdown_revision.headers["ETag"]
            body = {"output": output}
            start_headers = {
                "X-CSRF-Token": "csrf",
                "If-Match": draft_etag,
                "Idempotency-Key": "generate-1",
            }
            created = client.post(
                f"{base}/revisions/{source_revision_id}/generations",
                json=body,
                headers=start_headers,
            )
            assert created.status_code == 202, created.text
            generation = created.json()
            assert generation["status"] == "queued"
            assert generation["job_id"] == str(job.id)
            assert generation["source_revision_id"] == source_revision_id
            assert generation["publishable"] is False
            request = jobs.submit.call_args.args[0]
            assert scanner.scan.call_args.args[0] == request.source
            if archive:
                assert request.source_kind is SourceKind.ARCHIVE
                with ZipFile(BytesIO(request.source)) as package:
                    assert "manifest.json" not in package.namelist()
                    assert "assets/pixel.png" in package.namelist()
                    assert {info.date_time for info in package.infolist()} == {
                        (1980, 1, 1, 0, 0, 0)
                    }
                    with Image.open(BytesIO(package.read("assets/pixel.png"))) as pixel:
                        assert pixel.getpixel((0, 0)) == (255, 0, 0)
                    assert (
                        package.read("document.md")
                        == store.get_draft(owner.id, draft.id).content.encode()
                    )
            else:
                assert request.source_kind is SourceKind.MARKDOWN
                assert request.source == draft.content.encode()
            if output == "docx" and source_kind == "reversion_zip":
                sleep(2.1)
            assert (
                client.post(
                    f"{base}/revisions/{source_revision_id}/generations",
                    json=body,
                    headers=start_headers,
                ).json()["id"]
                == generation["id"]
            )
            reused = client.post(
                f"{base}/revisions/{source_revision_id}/generations",
                json={"output": "pptx" if output != "pptx" else "docx"},
                headers=start_headers,
            )
            assert reused.status_code == 412
            listed = client.get(f"{base}/generations?limit=20&offset=0")
            assert listed.status_code == 200
            assert listed.json()["generations"][0]["id"] == generation["id"]
            job.state = JobState.SUCCEEDED
            ready = client.get(f"{base}/generations/{generation['id']}")
            assert ready.json()["publishable"] is True
            publish_headers = {
                "X-CSRF-Token": "csrf",
                "If-Match": draft_etag,
                "Idempotency-Key": "native-1",
            }
            if output == "docx" and source_kind == "markdown":
                interrupted = mocker.patch.object(
                    store,
                    "attach_generation_revision",
                    side_effect=RuntimeError(
                        "simulated response loss after publication"
                    ),
                )
                with pytest.raises(RuntimeError, match="simulated response loss"):
                    client.post(
                        f"{base}/generations/{generation['id']}/publish",
                        headers=publish_headers,
                    )
                mocker.stop(interrupted)
                assert (
                    store.get_generation(
                        owner.id, draft.id, UUID(generation["id"])
                    ).result_revision_id
                    is None
                )
            published = client.post(
                f"{base}/generations/{generation['id']}/publish",
                headers=publish_headers,
            )
            assert published.status_code == 201, published.text
            revision = published.json()
            assert revision["operation"] == f"generate:{generation['id']}"
            assert (
                revision["approved_values"]
                == markdown_revision.json()["approved_values"]
            )
            semantic = client.get(
                f"{base}/revisions/{revision['id']}/diff",
                params={"from_revision_id": source_revision_id},
            )
            assert semantic.status_code == 200
            assert semantic.json()["scope"] == "approved_markdown"
            assert semantic.json()["status"] == "unchanged"
            assert "output" in semantic.json()["metadata_changes"]
            for kind in ("download", "preview"):
                artifact = client.get(
                    f"{base}/revisions/{revision['id']}/artifacts/{kind}"
                )
                assert artifact.content == result
                assert artifact.headers["X-Composer-Revision"] == revision["id"]
            if archive:
                assert (
                    client.get(
                        f"{base}/revisions/{revision['id']}/artifacts/source"
                    ).content
                    == source
                )
            if output == "pdf":
                assert (
                    client.get(
                        f"{base}/revisions/{revision['id']}/artifacts/traceability"
                    ).content
                    == b'{"trace":"exact"}'
                )
            assert (
                client.post(
                    f"{base}/generations/{generation['id']}/publish",
                    headers=publish_headers,
                ).json()
                == revision
            )
            assert (
                client.get(f"{base}/generations/{generation['id']}").json()[
                    "result_revision_id"
                ]
                == revision["id"]
            )
            assert store.get_draft(owner.id, draft.id).current_revision_id == UUID(
                revision["id"]
            )
            assert (
                store.cleanup_expired_drafts(
                    cutoff_at=datetime.now(UTC) + timedelta(seconds=1), limit=1
                )
                == 1
            )
    finally:
        engine.dispose()


def test_generation_failed_or_stale_job_never_replaces_current_revision(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, store, owner, jobs, _scanner, client = _client(tmp_path, mocker)
    try:
        draft = store.create_draft_with_source(
            owner.id,
            b"# Original\n",
            "scanner-approved",
            title="Draft",
            content="# Approved\n",
            media_type="text/markdown",
        )
        base = f"/api/v1/composer/drafts/{draft.id}"
        job = SimpleNamespace(
            id=UUID(int=101),
            owner_id=owner.id,
            source_sha256=hashlib.sha256(draft.content.encode()).hexdigest(),
            output=JobOutput.DOCX,
            state=JobState.FAILED,
            template_id=None,
            template_version_id=None,
        )
        jobs.submit.side_effect = lambda request, key: _fake_submit(job, request, key)
        jobs.get_visible.return_value = job
        jobs.download.side_effect = JobConflictError(
            "Conversion result is not available"
        )
        with client:
            approved = client.post(
                f"{base}/revisions/from-draft",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": draft.etag,
                    "Idempotency-Key": "approved-1",
                },
            )
            approved_id = approved.json()["id"]
            etag = approved.headers["ETag"]
            started = client.post(
                f"{base}/revisions/{approved_id}/generations",
                json={"output": "docx"},
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": etag,
                    "Idempotency-Key": "generate-1",
                },
            )
            assert started.status_code == 202
            generation_id = started.json()["id"]
            publish_url = f"{base}/generations/{generation_id}/publish"
            publish_headers = {
                "X-CSRF-Token": "csrf",
                "If-Match": etag,
                "Idempotency-Key": "native-1",
            }
            failed = client.post(publish_url, headers=publish_headers)
            assert failed.status_code == 409
            jobs.download.assert_called_once()
            jobs.download.reset_mock()
            jobs.download.side_effect = None
            job.state = JobState.SUCCEEDED
            store.save_draft(
                owner.id,
                draft.id,
                if_match=etag,
                title="Human edit",
                content="# Human precedence\n",
            )
            stale = client.post(publish_url, headers=publish_headers)
            assert stale.status_code == 412
            jobs.download.assert_not_called()
            assert store.get_draft(owner.id, draft.id).current_revision_id == UUID(
                approved_id
            )
    finally:
        engine.dispose()


def test_cancelled_generation_cannot_publish(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, store, owner, jobs, _scanner, client = _client(tmp_path, mocker)
    try:
        draft = store.create_draft_with_source(
            owner.id,
            b"# Approved\n",
            "scanner-approved",
            title="Draft",
            content="# Approved\n",
            media_type="text/markdown",
        )
        job = SimpleNamespace(
            id=UUID(int=104),
            owner_id=owner.id,
            source_sha256=hashlib.sha256(draft.content.encode()).hexdigest(),
            output=JobOutput.DOCX,
            state=JobState.QUEUED,
            template_id=None,
            template_version_id=None,
        )
        jobs.submit.side_effect = lambda request, key: _fake_submit(job, request, key)
        jobs.get_visible.return_value = job
        jobs.download.side_effect = JobConflictError("Result is not available")

        def cancel(*_args, **_kwargs):
            job.state = JobState.CANCELLED
            return job

        jobs.cancel.side_effect = cancel
        base = f"/api/v1/composer/drafts/{draft.id}"
        with client:
            approved = client.post(
                f"{base}/revisions/from-draft",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": draft.etag,
                    "Idempotency-Key": "approved",
                },
            )
            etag = approved.headers["ETag"]
            started = client.post(
                f"{base}/revisions/{approved.json()['id']}/generations",
                json={"output": "docx"},
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": etag,
                    "Idempotency-Key": "generate",
                },
            )
            assert started.status_code == 202
            cancelled = client.delete(
                f"{base}/generations/{started.json()['id']}",
                headers={"X-CSRF-Token": "csrf"},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            assert cancelled.json()["publishable"] is False
            publication = client.post(
                f"{base}/generations/{started.json()['id']}/publish",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": etag,
                    "Idempotency-Key": "publish",
                },
            )
            assert publication.status_code == 409
            assert store.get_draft(owner.id, draft.id).current_revision_id == UUID(
                approved.json()["id"]
            )
    finally:
        engine.dispose()


def test_restart_retry_uses_frozen_options_and_rejects_changed_engine_or_job(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, store, owner, jobs, _scanner, client = _client(tmp_path, mocker)
    try:
        draft = store.create_draft_with_source(
            owner.id,
            b"## Slide\n",
            "scanner-approved",
            title="Slides",
            content="## Slide\n",
            media_type="text/markdown",
        )
        job = SimpleNamespace(
            id=UUID(int=105),
            owner_id=owner.id,
            source_sha256="",
            output=JobOutput.PPTX,
            state=JobState.SUCCEEDED,
            template_id=None,
            template_version_id=None,
        )
        jobs.get_visible.return_value = job
        jobs.download.return_value = (job, b"PK\x03\x04native result")
        base = f"/api/v1/composer/drafts/{draft.id}"
        with client:
            approved = client.post(
                f"{base}/revisions/from-draft",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": draft.etag,
                    "Idempotency-Key": "approved",
                },
            )
            etag = approved.headers["ETag"]
            url = f"{base}/revisions/{approved.json()['id']}/generations"
            payload = {
                "output": "pptx",
                "presentation_dialect": "marp",
                "slide_level": 3,
            }
            headers = {
                "X-CSRF-Token": "csrf",
                "If-Match": etag,
                "Idempotency-Key": "restart-generate",
            }
            jobs.submit.side_effect = RuntimeError(
                "simulated restart before job attach"
            )
            with pytest.raises(RuntimeError, match="simulated restart"):
                client.post(url, json=payload, headers=headers)
            reserved = store.list_generations(owner.id, draft.id)
            assert len(reserved) == 1
            assert reserved[0].job_id is None
            original_versions = generation_routes.COMPONENT_VERSIONS
            drift = mocker.patch.object(
                generation_routes,
                "COMPONENT_VERSIONS",
                (("md-converter", "new-runtime"),),
            )
            jobs.submit.reset_mock()
            refused = client.post(url, json=payload, headers=headers)
            assert refused.status_code == 412
            jobs.submit.assert_not_called()
            mocker.stop(drift)
            assert original_versions == generation_routes.COMPONENT_VERSIONS
            wrong_default = mocker.patch.object(
                generation_routes,
                "_options",
                return_value=PresentationOptions(PresentationDialect.AUTO, 2),
            )
            jobs.submit.side_effect = lambda request, key: _fake_submit(
                job, request, key
            )
            started = client.post(url, json=payload, headers=headers)
            assert started.status_code == 202, started.text
            request = jobs.submit.call_args.args[0]
            assert request.component_versions == original_versions
            assert request.presentation_options == PresentationOptions(
                PresentationDialect.MARP, 3
            )
            mocker.stop(wrong_default)
            publish_url = f"{base}/generations/{started.json()['id']}/publish"
            publish_headers = {
                "X-CSRF-Token": "csrf",
                "If-Match": etag,
                "Idempotency-Key": "publish",
            }
            job.component_versions = (("md-converter", "wrong-receipt"),)
            assert client.post(publish_url, headers=publish_headers).status_code == 412
            job.component_versions = original_versions
            job.presentation_options = PresentationOptions(PresentationDialect.AUTO, 2)
            assert client.post(publish_url, headers=publish_headers).status_code == 412
            job.presentation_options = PresentationOptions(PresentationDialect.MARP, 3)
            published = client.post(publish_url, headers=publish_headers)
            assert published.status_code == 201, published.text
    finally:
        engine.dispose()


def test_accepted_proposal_then_human_edit_generates_both_changes(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, store, owner, jobs, _scanner, client = _client(tmp_path, mocker)
    try:
        draft = store.create_draft_with_source(
            owner.id,
            b"# Original\n",
            "scanner-approved",
            title="Draft",
            content="# Original\n",
            media_type="text/markdown",
        )
        proposal = store.create_proposal(
            owner.id,
            draft.id,
            base_version=draft.version,
            proposed_value="# Accepted\n",
            provenance="reviewed-suggestion",
        )
        store.decide_proposal(
            owner.id,
            draft.id,
            proposal.id,
            state=ProposalState.ACCEPTED,
            decided_value=None,
            if_match=store.get_draft(owner.id, draft.id).etag,
        )
        job = SimpleNamespace(
            id=UUID(int=102),
            owner_id=owner.id,
            source_sha256="",
            output=JobOutput.DOCX,
            state=JobState.QUEUED,
            template_id=None,
            template_version_id=None,
        )

        jobs.submit.side_effect = lambda request, key: _fake_submit(job, request, key)
        jobs.get_visible.return_value = job
        base = f"/api/v1/composer/drafts/{draft.id}"
        with client:
            accepted = client.post(
                f"{base}/proposals/{proposal.id}/publish",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": store.get_draft(owner.id, draft.id).etag,
                    "Idempotency-Key": "accepted-publication",
                },
            )
            assert accepted.status_code == 201, accepted.text
            assert store.get_draft(owner.id, draft.id).content == "# Accepted\n"
            edited = store.save_draft(
                owner.id,
                draft.id,
                if_match=accepted.headers["ETag"],
                title="Draft",
                content="# Accepted\n\nHuman edit.\n",
            )
            approved = client.post(
                f"{base}/revisions/from-draft",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": edited.etag,
                    "Idempotency-Key": "edited-publication",
                },
            )
            assert approved.status_code == 201, approved.text
            assert (
                json.loads(approved.json()["render_options"])["parent_revision_id"]
                == accepted.json()["id"]
            )
            started = client.post(
                f"{base}/revisions/{approved.json()['id']}/generations",
                json={"output": "docx"},
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": approved.headers["ETag"],
                    "Idempotency-Key": "generate-edited",
                },
            )
            assert started.status_code == 202, started.text
            assert (
                jobs.submit.call_args.args[0].source == b"# Accepted\n\nHuman edit.\n"
            )
    finally:
        engine.dispose()


def test_human_edit_keeps_prior_model_receipt_as_lineage_only(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, store, owner, jobs, _scanner, client = _client(tmp_path, mocker)
    try:
        draft = store.create_draft_with_source(
            owner.id,
            b"# Original\n",
            "scanner-approved",
            title="Draft",
            content="# Original\n",
            media_type="text/markdown",
        )
        prior = store.publish_revision(
            owner.id,
            draft.id,
            actor_id=owner.id,
            if_match=draft.etag,
            idempotency_key="approved-model",
            snapshot=RevisionSnapshot(
                draft.source,
                None,
                json.dumps({"content": "# Original\n"}),
                "{}",
                "provider:model:receipt",
                "human:accepted:model-step",
                "fixture-approved-model",
            ),
            artifacts=(
                ArtifactContent("download", "text/markdown", b"# Original\n"),
                ArtifactContent("preview", "text/markdown", b"# Original\n"),
            ),
        )
        edited = store.save_draft(
            owner.id,
            draft.id,
            if_match=store.get_draft(owner.id, draft.id).etag,
            title="Draft",
            content="# Original\n\nHuman edit.\n",
        )
        job = SimpleNamespace(
            id=UUID(int=103),
            owner_id=owner.id,
            source_sha256=hashlib.sha256(edited.content.encode()).hexdigest(),
            output=JobOutput.DOCX,
            state=JobState.SUCCEEDED,
            template_id=None,
            template_version_id=None,
        )
        jobs.submit.side_effect = lambda request, key: _fake_submit(job, request, key)
        jobs.get_visible.return_value = job
        jobs.download.return_value = (job, b"PK\x03\x04native result")
        base = f"/api/v1/composer/drafts/{draft.id}"
        with client:
            approved = client.post(
                f"{base}/revisions/from-draft",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": edited.etag,
                    "Idempotency-Key": "human-publish",
                },
            )
            assert approved.status_code == 201, approved.text
            assert approved.json()["model_identity"] is None
            lineage = json.loads(approved.json()["render_options"])
            assert lineage == {
                "parent_revision_id": str(prior.id),
                "lineage_model_identity": "provider:model:receipt",
            }
            started = client.post(
                f"{base}/revisions/{approved.json()['id']}/generations",
                json={"output": "docx"},
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": approved.headers["ETag"],
                    "Idempotency-Key": "native-generate",
                },
            )
            assert started.status_code == 202, started.text
            native = client.post(
                f"{base}/generations/{started.json()['id']}/publish",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": approved.headers["ETag"],
                    "Idempotency-Key": "native-publish",
                },
            )
            assert native.status_code == 201, native.text
            assert native.json()["model_identity"] is None
            native_options = json.loads(native.json()["render_options"])
            assert native_options["parent_approved_revision_id"] == str(prior.id)
            assert native_options["lineage_model_identity"] == "provider:model:receipt"
            assert (
                json.loads(native.json()["approved_values"])["content"]
                == edited.content
            )
            next_approval = client.post(
                f"{base}/revisions/from-draft",
                headers={
                    "X-CSRF-Token": "csrf",
                    "If-Match": native.headers["ETag"],
                    "Idempotency-Key": "human-publish-again",
                },
            )
            assert next_approval.status_code == 201, next_approval.text
            next_lineage = json.loads(next_approval.json()["render_options"])
            assert next_lineage == {
                "parent_revision_id": native.json()["id"],
                "lineage_model_identity": "provider:model:receipt",
            }
    finally:
        engine.dispose()
