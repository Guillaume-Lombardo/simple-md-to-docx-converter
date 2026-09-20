"""Real Pandoc worker publication for Markdown and image ZIP presentations."""

import io
import json
import zipfile

import pytest
from PIL import Image

from markweave.conversion.processor import build_production_processor
from tests.functional.test_presentation_api import (
    authenticate,
    client_for,
    settings_for,
)


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
