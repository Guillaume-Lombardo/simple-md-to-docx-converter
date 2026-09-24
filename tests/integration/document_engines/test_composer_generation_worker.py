"""Approved Composer Markdown crosses the real document worker and publication boundary."""

import hashlib
import json
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from markweave.app import create_app
from markweave.conversion.processor import build_production_processor
from markweave.malware import TrustingUploadScanner
from tests.functional.test_presentation_api import settings_for


@pytest.mark.integration
@pytest.mark.requires_pandoc
@pytest.mark.parametrize(
    "output",
    [
        "docx",
        pytest.param("pdf", marks=pytest.mark.requires_libreoffice),
        "pptx",
    ],
)
def test_approved_markdown_generates_exact_native_preview_and_download(
    tmp_path, output
):
    settings = settings_for(tmp_path, pandoc="pandoc").model_copy(
        update={
            "template_libreoffice_executable": "soffice",
            "composer_upload_max_bytes": 1_000_000,
            "composer_http_request_max_bytes": 1_100_000,
        }
    )
    app = create_app(settings, scanner=TrustingUploadScanner())
    with TestClient(app, base_url="https://testserver") as client:
        login = client.post(
            "/api/v1/login",
            json={"username": "admin", "password": "test-password"},
        )
        assert login.status_code == 200
        owner_id = UUID(login.json()["user"]["id"])
        csrf = login.json()["csrf_token"]
        store = app.state.components.composer_store
        assert store is not None
        markdown = "# Approved\n\nNative exact output.\n"
        draft = store.create_draft_with_source(
            owner_id,
            b"# Original\n",
            "test-scanned-source",
            title="Approved",
            content=markdown,
            media_type="text/markdown",
        )
        base = f"/api/v1/composer/drafts/{draft.id}"
        approved = client.post(
            f"{base}/revisions/from-draft",
            headers={
                "X-CSRF-Token": csrf,
                "If-Match": draft.etag,
                "Idempotency-Key": f"approve-{output}",
            },
        )
        assert approved.status_code == 201, approved.text
        started = client.post(
            f"{base}/revisions/{approved.json()['id']}/generations",
            json={"output": output},
            headers={
                "X-CSRF-Token": csrf,
                "If-Match": approved.headers["ETag"],
                "Idempotency-Key": f"generate-{output}",
            },
        )
        assert started.status_code == 202, started.text
        components = app.state.components
        worker = components.build_conversion_worker(
            worker_id=f"composer-{output}",
            processor=build_production_processor(settings, components.object_store),
        )
        assert worker.run_once()
        ready = client.get(f"{base}/generations/{started.json()['id']}")
        assert ready.json()["status"] == "succeeded", ready.text
        assert ready.json()["publishable"] is True
        published = client.post(
            f"{base}/generations/{started.json()['id']}/publish",
            headers={
                "X-CSRF-Token": csrf,
                "If-Match": approved.headers["ETag"],
                "Idempotency-Key": f"publish-{output}",
            },
        )
        assert published.status_code == 201, published.text
        revision = published.json()
        preview = client.get(f"{base}/revisions/{revision['id']}/artifacts/preview")
        download = client.get(f"{base}/revisions/{revision['id']}/artifacts/download")
        result = client.get(f"/api/v1/conversions/{started.json()['job_id']}/result")
        assert preview.status_code == download.status_code == result.status_code == 200
        assert preview.content == download.content == result.content
        assert hashlib.sha256(preview.content).hexdigest() == next(
            item["sha256"]
            for item in revision["artifacts"]
            if item["kind"] == "preview"
        )
        assert json.loads(revision["approved_values"])["content"] == markdown
