"""Real-process coverage of ``markweave convert --local`` (T106, D1).

The installed console executable runs the real isolation launcher, version
probes, validation, and document engines. Tests that need an engine carry its
marker; the failure-path tests use small fake executables so missing-engine,
timeout, and cancellation behavior is deterministic on every host.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import time
import zipfile
from collections.abc import Mapping
from pathlib import Path

import pytest
from PIL import Image

pytestmark = pytest.mark.integration

EXAMPLE_ENVIRONMENT = Path("examples/local-conversion.env")
FONT_MANIFEST = Path("toolchain/document-engines/fonts/manifest.json").resolve()
TEMPLATE = base64.b64decode(
    Path("examples/quickstart-template.docx.base64").read_bytes()
)
CLI = Path(sys.executable).with_name("markweave")


def _environment(
    tmp_path: Path, *, private_workspace: bool = True, **overrides: str
) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith(("MARKWEAVE_", "MD_CONVERTER_"))
    }
    for line in EXAMPLE_ENVIRONMENT.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, value = line.split("=", 1)
            environment[name] = value
    if private_workspace:
        workspace = tmp_path / "engine-workspace"
        workspace.mkdir(exist_ok=True)
        environment["MARKWEAVE_TEMPLATE_ENGINE_WORKSPACE_ROOT"] = str(workspace)
    environment.update(
        {
            "MARKWEAVE_CONVERSION_FONT_MANIFEST_PATH": str(FONT_MANIFEST),
            "MARKWEAVE_TEMPLATE_ENGINE_TIMEOUT_SECONDS": "120",
            "MARKWEAVE_CONVERSION_PDF_MAX_PAGES": "20",
            "MARKWEAVE_CONVERSION_MERMAID_EXECUTABLE": os.environ.get(
                "MARKWEAVE_TEST_MMDC", "mmdc"
            ),
            "MARKWEAVE_CONVERSION_CHROMIUM_EXECUTABLE": os.environ.get(
                "MARKWEAVE_TEST_CHROMIUM", "/usr/bin/google-chrome-stable"
            ),
            **overrides,
        }
    )
    return environment


def _run(
    environment: Mapping[str, str], *arguments: str, timeout: float = 300
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        (str(CLI), "--json", *arguments),
        capture_output=True,
        check=False,
        env=dict(environment),
        text=True,
        timeout=timeout,
    )


def _inputs(tmp_path: Path, markdown: str = "# Local\n\nReal **conversion**.\n"):
    source = tmp_path / "document.md"
    source.write_text(markdown, encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    return source, output


def _error(completed: subprocess.CompletedProcess[str]) -> dict[str, str]:
    assert completed.returncode == 1, completed.stderr
    assert completed.stdout == ""
    return json.loads(completed.stderr)["error"]


def _fake_engine(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o700)
    return path


def _slow_pandoc(tmp_path: Path) -> tuple[Path, Path]:
    pid_file = tmp_path / "engine.pid"
    engine = _fake_engine(
        tmp_path,
        "slow-pandoc",
        'if [ "$1" = --version ]; then echo "pandoc 3.10.2"; exit 0; fi\n'
        f'echo $$ > "{pid_file}"\nexec sleep 120',
    )
    return engine, pid_file


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for(path: Path, timeout: float = 30.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and (content := path.read_text().strip()):
            return int(content)
        time.sleep(0.05)
    raise AssertionError("The fake engine did not start.")


def _docx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


@pytest.mark.requires_pandoc
def test_real_pandoc_local_docx_with_template_and_required_pins(
    tmp_path: Path,
) -> None:
    source, output = _inputs(tmp_path)
    template = tmp_path / "template.docx"
    template.write_bytes(TEMPLATE)

    completed = _run(
        _environment(tmp_path),
        "convert",
        str(source),
        "--local",
        "--template",
        str(template),
        "--require-pinned",
        "-o",
        str(output),
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["engines"] == [
        {
            "actual_version": "3.10.2",
            "approved_version": "3.10.2",
            "name": "pandoc",
            "status": "pinned",
        }
    ]
    assert result["warnings"] == []
    assert sorted(path.name for path in output.iterdir()) == [
        "document.docx",
        "document.traceability.json",
    ]
    assert "Real" in _docx_text((output / "document.docx").read_bytes())
    manifest = json.loads((output / "document.traceability.json").read_bytes())
    assert manifest["engines_pinned"] is True
    assert manifest["template"]["mode"] == "local-file"
    assert manifest["source"]["kind"] == "markdown"
    assert str(tmp_path) not in json.dumps(manifest)
    assert list((tmp_path / "engine-workspace").iterdir()) == []


@pytest.mark.requires_pandoc
def test_real_pandoc_local_archive_binds_normalized_images(tmp_path: Path) -> None:
    image = io.BytesIO()
    Image.new("RGB", (8, 8), (12, 34, 56)).save(image, format="PNG")
    package = tmp_path / "bundle.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("document.md", "# Packaged\n\n![Pixel](assets/pixel.png)\n")
        archive.writestr("assets/pixel.png", image.getvalue())
    output = tmp_path / "out"
    output.mkdir()

    completed = _run(
        _environment(tmp_path), "convert", str(package), "--local", "-o", str(output)
    )

    assert completed.returncode == 0, completed.stderr
    with zipfile.ZipFile(output / "bundle.docx") as archive:
        assert any(name.startswith("word/media/") for name in archive.namelist())
    manifest = json.loads((output / "bundle.traceability.json").read_bytes())
    assert manifest["source"]["kind"] == "archive"


@pytest.mark.requires_pandoc
@pytest.mark.parametrize(
    ("markdown", "message"),
    [
        ("<b>raw</b>\n", "Markdown input contains raw HTML."),
        ("![x](https://example.com/x.png)\n", None),
    ],
)
def test_invalid_local_input_fails_before_publication(
    tmp_path: Path, markdown: str, message: str | None
) -> None:
    source, output = _inputs(tmp_path, markdown)
    error = _error(
        _run(
            _environment(tmp_path), "convert", str(source), "--local", "-o", str(output)
        )
    )
    assert error["code"] == "validation"
    if message is not None:
        assert error["message"] == message
    assert list(output.iterdir()) == []


def test_missing_required_engine_fails_clearly(tmp_path: Path) -> None:
    source, output = _inputs(tmp_path)
    error = _error(
        _run(
            _environment(
                tmp_path,
                MARKWEAVE_TEMPLATE_PANDOC_EXECUTABLE=str(tmp_path / "missing-pandoc"),
            ),
            "convert",
            str(source),
            "--local",
            "-o",
            str(output),
        )
    )
    assert error == {
        "code": "engine_missing",
        "message": "The required Pandoc engine is missing or cannot run.",
    }
    assert list(output.iterdir()) == []


def test_missing_libreoffice_is_required_only_for_pdf(tmp_path: Path) -> None:
    source, output = _inputs(tmp_path)
    environment = _environment(
        tmp_path,
        MARKWEAVE_TEMPLATE_PANDOC_EXECUTABLE=str(
            _fake_engine(tmp_path, "pandoc", "echo 'pandoc 3.10.2'")
        ),
        MARKWEAVE_TEMPLATE_LIBREOFFICE_EXECUTABLE=str(tmp_path / "missing-soffice"),
    )
    error = _error(
        _run(
            environment,
            "convert",
            str(source),
            "--local",
            "--to",
            "docx,pdf",
            "-o",
            str(output),
        )
    )
    assert error["code"] == "engine_missing"
    assert "LibreOffice" in error["message"]
    assert list(output.iterdir()) == []


@pytest.mark.requires_pandoc
def test_unpinned_engine_warns_or_fails_with_require_pinned(tmp_path: Path) -> None:
    source, output = _inputs(tmp_path)
    wrapper = _fake_engine(
        tmp_path,
        "pandoc-wrapper",
        'if [ "$1" = --version ]; then echo "pandoc 3.9.0"; exit 0; fi\n'
        'exec pandoc "$@"',
    )
    environment = _environment(
        tmp_path, MARKWEAVE_TEMPLATE_PANDOC_EXECUTABLE=str(wrapper)
    )

    refused = _run(
        environment,
        "convert",
        str(source),
        "--local",
        "--require-pinned",
        "-o",
        str(output),
    )
    assert _error(refused) == {
        "code": "engine_version_unpinned",
        "message": "Pandoc 3.9.0 differs from the approved pin 3.10.2.",
    }
    assert list(output.iterdir()) == []

    human = subprocess.run(
        (str(CLI), "convert", str(source), "--local", "-o", str(output)),
        capture_output=True,
        check=False,
        env=environment,
        text=True,
        timeout=300,
    )
    assert human.returncode == 0, human.stderr
    assert human.stderr == (
        "warning: Pandoc 3.9.0 differs from the approved pin 3.10.2.\n"
    )
    manifest = json.loads((output / "document.traceability.json").read_bytes())
    assert manifest["engines"][0]["actual_version"] == "3.9.0"
    assert manifest["engines_pinned"] is False


def test_global_timeout_terminates_the_engine_process_group(tmp_path: Path) -> None:
    source, output = _inputs(tmp_path)
    engine, pid_file = _slow_pandoc(tmp_path)
    started = time.monotonic()

    error = _error(
        _run(
            _environment(tmp_path, MARKWEAVE_TEMPLATE_PANDOC_EXECUTABLE=str(engine)),
            "--timeout",
            "3",
            "convert",
            str(source),
            "--local",
            "-o",
            str(output),
            timeout=60,
        )
    )

    assert error == {
        "code": "conversion_timeout",
        "message": "The local conversion timed out.",
    }
    assert time.monotonic() - started < 30
    assert not _process_exists(_wait_for(pid_file))
    assert list(output.iterdir()) == []
    assert list((tmp_path / "engine-workspace").iterdir()) == []


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signal_cancels_the_running_engine_without_outputs(
    tmp_path: Path, signum: signal.Signals
) -> None:
    source, output = _inputs(tmp_path)
    engine, pid_file = _slow_pandoc(tmp_path)
    process = subprocess.Popen(
        (str(CLI), "--json", "convert", str(source), "--local", "-o", str(output)),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_environment(tmp_path, MARKWEAVE_TEMPLATE_PANDOC_EXECUTABLE=str(engine)),
        text=True,
    )
    try:
        engine_pid = _wait_for(pid_file)
        process.send_signal(signum)
        stdout, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert process.returncode == 1
    assert stdout == ""
    assert json.loads(stderr)["error"] == {
        "code": "conversion_cancelled",
        "message": "The local conversion was cancelled.",
    }
    assert not _process_exists(engine_pid)
    assert list(output.iterdir()) == []
    assert list((tmp_path / "engine-workspace").iterdir()) == []


@pytest.mark.requires_pandoc
@pytest.mark.requires_libreoffice
def test_real_libreoffice_local_pdf_records_actual_versions(tmp_path: Path) -> None:
    source, output = _inputs(tmp_path)
    completed = _run(
        _environment(tmp_path),
        "convert",
        str(source),
        "--local",
        "--to",
        "docx,pdf",
        "--require-pinned",
        "-o",
        str(output),
    )

    assert completed.returncode == 0, completed.stderr
    pdf = (output / "document.pdf").read_bytes()
    assert pdf.startswith(b"%PDF-")
    manifest = json.loads((output / "document.traceability.json").read_bytes())
    assert [engine["name"] for engine in manifest["engines"]] == [
        "pandoc",
        "libreoffice",
    ]
    assert manifest["engines_pinned"] is True
    assert manifest["pdf"]["libreoffice_version"] == "26.2.5.2"
    assert manifest["pdf"]["pandoc_version"] == "3.10.2"
    assert manifest["pdf"]["mermaid_version"] == "not-used"
    assert manifest["pdf"]["template_mode"] == "pandoc-default"
    assert [item["format"] for item in manifest["outputs"]] == ["docx", "pdf"]


@pytest.mark.requires_pandoc
@pytest.mark.requires_mermaid
def test_real_mermaid_local_conversion_records_diagram_engines(
    tmp_path: Path,
) -> None:
    source, output = _inputs(
        tmp_path, "# Diagram\n\n```mermaid\nflowchart LR\n  A --> B\n```\n"
    )
    # Like the Mermaid adapter tests, keep the default system temporary root: the
    # sandboxed Chromium renderer cannot traverse pytest's owner-only directories.
    completed = _run(
        _environment(tmp_path, private_workspace=False),
        "convert",
        str(source),
        "--local",
        "-o",
        str(output),
    )

    assert completed.returncode == 0, completed.stderr
    with zipfile.ZipFile(output / "document.docx") as archive:
        assert any(name.startswith("word/media/") for name in archive.namelist())
    manifest = json.loads((output / "document.traceability.json").read_bytes())
    engines = {engine["name"]: engine for engine in manifest["engines"]}
    assert set(engines) == {"pandoc", "mermaid-cli", "chromium"}
    assert engines["chromium"]["actual_version"] == "151.0.7922.173"
    assert engines["mermaid-cli"]["actual_version"]


@pytest.mark.requires_pandoc
def test_failed_overwrite_keeps_the_previous_complete_output_set(
    tmp_path: Path,
) -> None:
    source, output = _inputs(tmp_path)
    previous = {
        "document.docx": b"previous docx",
        "document.traceability.json": b"previous manifest",
    }
    for name, content in previous.items():
        (output / name).write_bytes(content)
    output.chmod(0o500)
    try:
        error = _error(
            _run(
                _environment(tmp_path),
                "convert",
                str(source),
                "--local",
                "--overwrite",
                "-o",
                str(output),
            )
        )
    finally:
        output.chmod(0o700)

    assert error["code"] == "output_write_failed"
    assert {path.name: path.read_bytes() for path in output.iterdir()} == previous


_INTERRUPTED_PUBLICATION = """
import os
import signal
import sys

from markweave.cli.main import main

mode = sys.argv[1]
original = os.replace
replaced = 0


def replace(source, target, **options):
    global replaced
    if str(source).endswith(".previous"):
        return original(source, target, **options)
    replaced += 1
    if mode == "failure" and replaced == 2:
        raise OSError("injected publication failure")
    original(source, target, **options)
    if mode == "signal" and replaced == 1:
        os.kill(os.getpid(), signal.SIGTERM)


os.replace = replace
raise SystemExit(main(sys.argv[2:]))
"""


def _previous_set(output: Path) -> dict[str, bytes]:
    previous = {
        "document.docx": b"previous docx",
        "document.pdf": b"previous pdf",
        "document.traceability.json": b"previous manifest",
    }
    for name, content in previous.items():
        (output / name).write_bytes(content)
    return previous


@pytest.mark.requires_pandoc
@pytest.mark.parametrize(
    ("mode", "code"),
    [("signal", "conversion_cancelled"), ("failure", "output_write_failed")],
)
def test_interruption_after_a_replacement_restores_the_previous_set(
    tmp_path: Path, mode: str, code: str
) -> None:
    source, output = _inputs(tmp_path)
    previous = _previous_set(output)
    completed = subprocess.run(
        (
            sys.executable,
            "-c",
            _INTERRUPTED_PUBLICATION,
            mode,
            "--json",
            "convert",
            str(source),
            "--local",
            "--overwrite",
            "-o",
            str(output),
        ),
        capture_output=True,
        check=False,
        env=_environment(tmp_path),
        text=True,
        timeout=300,
    )

    assert _error(completed)["code"] == code
    assert {path.name: path.read_bytes() for path in output.iterdir()} == previous


@pytest.mark.requires_pandoc
@pytest.mark.parametrize("outcome", ["released", "cancelled"])
def test_overlapping_publication_waits_for_the_output_directory_lock(
    tmp_path: Path, outcome: str
) -> None:
    source, output = _inputs(tmp_path)
    previous = _previous_set(output)
    holder = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        process = subprocess.Popen(
            (
                str(CLI),
                "--json",
                "convert",
                str(source),
                "--local",
                "--overwrite",
                "-o",
                str(output),
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_environment(tmp_path),
            text=True,
        )
        time.sleep(3)
        assert process.poll() is None, "publication must wait for the lock"
        assert {path.name: path.read_bytes() for path in output.iterdir()} == previous
        if outcome == "released":
            fcntl.flock(holder, fcntl.LOCK_UN)
        else:
            process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=120)
    finally:
        os.close(holder)

    if outcome == "released":
        assert process.returncode == 0, stderr
        assert json.loads(stdout)["status"] == "succeeded"
        manifest = json.loads((output / "document.traceability.json").read_bytes())
        docx = (output / "document.docx").read_bytes()
        assert manifest["outputs"][0]["sha256"] == hashlib.sha256(docx).hexdigest()
        assert (output / "document.pdf").read_bytes() == previous["document.pdf"]
    else:
        assert process.returncode == 1
        assert json.loads(stderr)["error"]["code"] == "conversion_cancelled"
        assert {path.name: path.read_bytes() for path in output.iterdir()} == previous
