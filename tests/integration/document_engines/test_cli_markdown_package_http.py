"""CLI Markdown-with-local-images packaging against the real service (T104).

The client reads real files, packages them into the approved archive layout, and
crosses only the HTTP API. A real worker then applies the service's archive,
path, image, and limit validation to the uploaded package.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from threading import Thread
from time import sleep
from typing import Any

import pytest
import uvicorn
from PIL import Image

from markweave.app import create_app
from markweave.cli.http import HttpTransport
from markweave.cli.main import main
from markweave.cli.markdown_package import (
    ESCAPING_IMAGE_PATH,
    MISSING_IMAGE,
    build_package,
)
from markweave.cli.profiles import ProfileStore
from markweave.cli.types import ConnectionProfile
from markweave.config import Settings
from markweave.conversion.processor import build_production_processor
from markweave.malware import TrustingUploadScanner
from tests.settings import template_settings

pytestmark = [pytest.mark.integration]

UPLOAD_LIMIT = 200_000


def _png(width: int = 12, height: int = 8) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), "#336699").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def service(tmp_path: Path, monkeypatch) -> Iterator[tuple[str, Any, Settings]]:
    """Run the standalone assembly over loopback HTTP with a signed-in profile."""
    settings = Settings(
        **template_settings(template_pandoc_executable="pandoc"),
        initial_admin_username="admin",
        initial_admin_password="admin-password",  # noqa: S106
        argon2_memory_cost=8,
        argon2_time_cost=1,
        argon2_parallelism=1,
        storage_profile="standalone",
        standalone_data_directory=tmp_path / "data",
        conversion_upload_max_bytes=UPLOAD_LIMIT,
        conversion_request_max_bytes=UPLOAD_LIMIT + 100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    application = create_app(settings, scanner=TrustingUploadScanner())
    config = uvicorn.Config(application, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    with config.bind_socket() as listener:
        port = listener.getsockname()[1]
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            for _ in range(500):
                if server.started:
                    break
                sleep(0.01)
            assert server.started
            url = f"http://127.0.0.1:{port}"
            response = HttpTransport(url, verify_tls=False, timeout=5).login(
                "admin", "admin-password"
            )
            assert response.status == 200
            assert response.session is not None and response.payload is not None
            ProfileStore().save(
                ConnectionProfile(
                    "default", url, response.session, response.payload["csrf_token"]
                )
            )
            yield url, application, settings
        finally:
            server.should_exit = True
            thread.join(timeout=5)


def _submit(
    source: Path, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    code = main(("--json", "convert", str(source), "--output", "docx"))
    captured = capsys.readouterr()
    stream = captured.out if code == 0 else captured.err
    value = json.loads(stream.splitlines()[-1])
    assert isinstance(value, dict)
    return code, value


def _process(application: Any, settings: Settings) -> None:
    components = application.state.components
    worker = components.build_conversion_worker(
        worker_id="t104-integration",
        processor=build_production_processor(settings, components.object_store),
    )
    assert worker.run_once()


def _job(job_id: str, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert main(("--json", "jobs", "show", job_id)) == 0
    value = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert isinstance(value, dict)
    return value


@pytest.mark.requires_pandoc
def test_cli_packages_markdown_and_local_images_into_a_real_docx(
    service, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _url, application, settings = service
    project = tmp_path / "project"
    (project / "assets").mkdir(parents=True)
    (project / "assets" / "diagram.png").write_bytes(_png())
    (project / "assets" / "unused.png").write_bytes(_png())
    source = project / "private-report.md"
    source.write_text(
        "# Packaged\n\n![Diagram](assets/diagram.png)\n\n"
        "Reused ![again](./assets/diagram.png).\n",
        encoding="utf-8",
    )

    code, submitted = _submit(source, capsys)

    assert code == 0, submitted
    assert submitted["packaged_images"] == 1
    assert submitted["source_filename"] == "source.zip"
    _process(application, settings)
    capsys.readouterr()
    finished = _job(submitted["id"], capsys)
    assert finished["state"] == "succeeded", finished
    result = tmp_path / "result.docx"
    assert main(("jobs", "download", submitted["id"], str(result))) == 0
    with zipfile.ZipFile(result) as document:
        media = {
            document.read(name)
            for name in document.namelist()
            if name.startswith("word/media/")
        }
    # Both references embed the one normalized PNG; the unused image is absent.
    assert len(media) == 1
    assert next(iter(media)).startswith(b"\x89PNG")
    output = capsys.readouterr()
    assert source.name not in output.out + output.err


@pytest.mark.light_coverage
@pytest.mark.parametrize(
    ("markdown", "message"),
    [
        ("![m](assets/missing.png)", MISSING_IMAGE),
        ("![t](../outside.png)", ESCAPING_IMAGE_PATH),
    ],
)
def test_client_precheck_and_service_validation_share_stable_errors(
    service,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    markdown: str,
    message: str,
) -> None:
    """The client refuses early; the service refuses the same package itself."""
    _url, application, settings = service
    project = tmp_path / "project"
    (project / "assets").mkdir(parents=True)
    (tmp_path / "outside.png").write_bytes(_png())
    source = project / "doc.md"
    source.write_text(markdown, encoding="utf-8")

    code, refused = _submit(source, capsys)

    assert code == 1
    assert refused["error"] == {"code": "validation", "message": message}
    assert main(("--json", "jobs", "list")) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["items"] == []

    # Bypass the client pre-check with the same layout, including a member that
    # would satisfy the traversal: the service must still reject it.
    crafted = tmp_path / "crafted.zip"
    crafted.write_bytes(
        build_package(
            markdown.encode(),
            {PurePosixPath("assets/other.png"): _png()},
        )
    )
    code, submitted = _submit(crafted, capsys)
    assert code == 0, submitted
    _process(application, settings)
    capsys.readouterr()
    failed = _job(submitted["id"], capsys)
    assert failed["state"] == "failed"
    assert failed["error_code"] == "validation"
    assert failed["error_message"] == message


@pytest.mark.light_coverage
def test_service_rejects_a_traversal_member_in_a_packaged_archive(
    service, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _url, application, settings = service
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("document.md", "![t](../escape.png)")
        package.writestr("../escape.png", _png())
    crafted = tmp_path / "traversal.zip"
    crafted.write_bytes(archive.getvalue())

    code, submitted = _submit(crafted, capsys)

    assert code == 0, submitted
    _process(application, settings)
    capsys.readouterr()
    failed = _job(submitted["id"], capsys)
    assert (failed["state"], failed["error_code"], failed["error_message"]) == (
        "failed",
        "validation",
        "Document archive is invalid.",
    )


@pytest.mark.light_coverage
def test_oversize_packages_and_images_keep_the_service_limits(
    service, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _url, application, settings = service
    project = tmp_path / "project"
    project.mkdir()
    (project / "large.png").write_bytes(b"\x89PNG" + b"\0" * UPLOAD_LIMIT)
    too_large = project / "too-large.md"
    too_large.write_text("![l](large.png)", encoding="utf-8")

    code, rejected = _submit(too_large, capsys)

    assert code == 1
    assert rejected["error"]["code"] == "conversion_request_invalid"

    (project / "wide.png").write_bytes(_png(width=2_001, height=1))
    wide = project / "wide.md"
    wide.write_text("![w](wide.png)", encoding="utf-8")
    code, submitted = _submit(wide, capsys)
    assert code == 0, submitted
    _process(application, settings)
    capsys.readouterr()
    failed = _job(submitted["id"], capsys)
    assert (failed["state"], failed["error_code"], failed["error_message"]) == (
        "failed",
        "validation",
        "Document image exceeds configured limits.",
    )
