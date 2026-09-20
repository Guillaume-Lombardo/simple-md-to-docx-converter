"""PowerPoint admission, planning and durable worker contracts."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from markweave.app import create_app
from markweave.config import Settings
from markweave.conversion.processor import build_production_processor
from markweave.malware import TrustingUploadScanner
from tests.settings import template_settings

pytestmark = pytest.mark.functional


def settings_for(tmp_path: Path, *, pandoc: str = "/bin/true") -> Settings:
    return Settings(
        **template_settings(template_pandoc_executable=pandoc),
        initial_admin_username="admin",
        initial_admin_password="test-" + "password",
        argon2_memory_cost=8,
        argon2_time_cost=1,
        storage_profile="standalone",
        standalone_data_directory=tmp_path,
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3600,
    )


def client_for(tmp_path: Path, *, pandoc: str = "/bin/true") -> TestClient:
    return TestClient(
        create_app(
            settings_for(tmp_path, pandoc=pandoc), scanner=TrustingUploadScanner()
        ),
        base_url="https://testserver",
    )


def authenticate(client):
    response = client.post(
        "/api/v1/login", json={"username": "admin", "password": "test-password"}
    )
    assert response.status_code == 200
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def test_presentation_plan_admission_idempotency_and_cancellation(tmp_path):
    with client_for(tmp_path) as client:
        files = {
            "source": ("slides.md", b"## One\n\nText\n\n## Two\n\nEnd", "text/markdown")
        }
        assert client.post("/api/v1/presentation-plan", files=files).status_code == 401
        headers = authenticate(client)
        assert client.post("/api/v1/presentation-plan", files=files).status_code == 403
        plan = client.post("/api/v1/presentation-plan", headers=headers, files=files)
        assert plan.status_code == 200, plan.text
        assert plan.json()["titles"] == ["One", "Two"]
        assert client.get("/api/v1/templates?kind=pptx").json()["items"] == []
        assert (
            client.get("/api/v1/conversion-options?template_kind=pptx").json()[
                "selection_source"
            ]
            == "pandoc_default"
        )
        headers["Idempotency-Key"] = "presentation-request"
        data = {"output": "pptx", "slide_level": "3", "presentation_dialect": "marp"}
        job = client.post(
            "/api/v1/conversions", headers=headers, files=files, data=data
        )
        assert job.status_code == 202, job.text
        assert job.json()["source_filename"] == "slides.md"
        assert (
            client.get("/api/v1/conversions").json()["items"][0]["source_filename"]
            == "slides.md"
        )
        assert job.json()["template_mode"] == "pandoc-default"
        assert job.json()["presentation_options"] == {
            "dialect": "marp",
            "slide_level": 3,
        }
        replay = client.post(
            "/api/v1/conversions", headers=headers, files=files, data=data
        )
        assert replay.json()["id"] == job.json()["id"]
        changed = client.post(
            "/api/v1/conversions",
            headers=headers,
            files=files,
            data={**data, "slide_level": "2"},
        )
        assert changed.status_code == 409
        assert (
            client.post(
                "/api/v1/conversions",
                headers=headers,
                files=files,
                data={**data, "output": "docx"},
            ).status_code
            == 422
        )
        assert (
            client.delete(
                f"/api/v1/conversions/{job.json()['id']}", headers=headers
            ).json()["state"]
            == "cancelled"
        )


@pytest.mark.parametrize(
    "source",
    [
        b"<script>alert(1)</script>",
        b"![bad](https://example.com/image.png)",
        b"---\n- invalid\n---\n",
        b"\xff",
    ],
)
def test_presentation_plan_rejects_unsafe_or_invalid_input(tmp_path, source):
    with client_for(tmp_path) as client:
        response = client.post(
            "/api/v1/presentation-plan",
            headers=authenticate(client),
            files={"source": ("slides.md", source)},
        )
        assert response.status_code == 422


@pytest.mark.integration
@pytest.mark.requires_pandoc
@pytest.mark.parametrize("output", ["pptx", "pptx-bundle"])
@pytest.mark.parametrize("archive_input", [False, True])
def test_real_worker_generates_without_any_template(tmp_path, output, archive_input):
    with client_for(tmp_path, pandoc="pandoc") as client:
        headers = authenticate(client)
        app = client.app
        components = app.state.components
        # The application-owned settings are also the processor's actual configuration.
        settings = settings_for(tmp_path, pandoc="pandoc")
        worker = components.build_conversion_worker(
            worker_id="pptx-integration",
            processor=build_production_processor(settings, components.object_store),
        )
        reference = client.get("/api/v1/presentation-reference")
        assert reference.status_code == 200
        with zipfile.ZipFile(io.BytesIO(reference.content)) as package:
            assert "ppt/presentation.xml" in package.namelist()
        original = b"## First\n\nEditable text\n\n## Second\n\n- A bullet\n"
        filename = "slides.md"
        if archive_input:
            image = io.BytesIO()
            Image.new("RGB", (10, 10), "blue").save(image, format="PNG")
            package = io.BytesIO()
            with zipfile.ZipFile(package, "w") as source_zip:
                source_zip.writestr(
                    "slides.md", original + b"\n![Local image](image.png)\n"
                )
                source_zip.writestr("image.png", image.getvalue())
            original = package.getvalue()
            filename = "slides.zip"
        created = client.post(
            "/api/v1/conversions",
            headers=headers,
            files={"source": (filename, original)},
            data={"output": output},
        )
        assert created.status_code == 202, created.text
        job_id = created.json()["id"]
        assert worker.run_once()
        finished = client.get(f"/api/v1/conversions/{job_id}").json()
        assert finished["state"] == "succeeded", finished
        result = client.get(f"/api/v1/conversions/{job_id}/result")
        assert result.status_code == 200
        content = result.content
        if output == "pptx-bundle":
            with zipfile.ZipFile(io.BytesIO(content)) as bundle:
                assert (
                    bundle.read(
                        "source/source.zip" if archive_input else "source/document.md"
                    )
                    == original
                )
                assert (
                    json.loads(bundle.read("generation.json"))["template_mode"]
                    == "pandoc-default"
                )
                content = bundle.read("presentation.pptx")
        with zipfile.ZipFile(io.BytesIO(content)) as slides:
            if archive_input:
                assert any(name.startswith("ppt/media/") for name in slides.namelist())
            assert b"Editable text" in slides.read("ppt/slides/slide1.xml")
            assert b"A bullet" in slides.read("ppt/slides/slide2.xml")
