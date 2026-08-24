"""Real font, Pandoc, and LibreOffice validation for Word templates."""

from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import subprocess
import textwrap
import time
import zipfile
from contextlib import suppress
from pathlib import Path
from uuid import uuid4
from xml.etree import ElementTree

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from md_converter.auth.models import Role, User
from md_converter.persistence.migrations import upgrade_database
from md_converter.persistence.schema import TemplateVersionRow, UserRow
from md_converter.persistence.sql import create_database_engine
from md_converter.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from md_converter.storage import FilesystemObjectStore
from md_converter.templates import engines as template_engines
from md_converter.templates.engines import (
    TemplateActivationContext,
    TemplateEngineConfig,
    validate_template_for_activation,
)
from md_converter.templates.errors import (
    TemplateValidationError,
    TemplateValidationErrorCode,
)
from md_converter.templates.models import TemplateCreate, TemplateSearch
from md_converter.templates.service import TemplateService
from md_converter.templates.validation import (
    APPROVED_FONT_POLICY,
    TemplateFontDeclaration,
    TemplateLimits,
    validate_template,
)

pytestmark = pytest.mark.integration
LIMITS = TemplateLimits(
    5_000_000,
    500,
    2_000_000,
    20_000_000,
    200.0,
    100_000,
    100,
    500_000,
    32,
    100,
)
DEFAULT_REFERENCE_FONTS = TemplateFontDeclaration(
    (
        "Aptos",
        "Aptos Display",
        "Calibri",
        "Cambria",
        "Cambria Math",
        "Consolas",
        "Courier New",
        "Times New Roman",
    )
)


def _versioned_service(
    tmp_path: Path, context: TemplateActivationContext
) -> tuple[TemplateService, SqlTemplateCatalogRepository, User, Engine]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    owner = User(uuid4(), "Owner", "engine-owner", "hash", Role.USER)
    with Session(engine) as database, database.begin():
        database.add(
            UserRow(
                id=str(owner.id),
                username=owner.username,
                normalized_username=owner.normalized_username,
                password_hash=owner.password_hash,
                role=owner.role.value,
                active=True,
                auth_version=0,
            )
        )
    catalog = SqlTemplateCatalogRepository(engine)
    service = TemplateService(
        catalog=catalog,
        selections=SqlTemplateSelectionRepository(engine),
        objects=FilesystemObjectStore(tmp_path),
        validate_content=lambda data, declaration: validate_template_for_activation(
            data, declaration, context
        ),
    )
    return service, catalog, owner, engine


def _default_reference() -> bytes:
    return subprocess.run(
        ["pandoc", "--print-default-data-file", "reference.docx"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    ).stdout


def _candidate_reference() -> bytes:
    """Remove Pandoc sample links and unsupported dormant script mappings."""

    source_data = _default_reference()
    output = io.BytesIO()
    relationship_namespace = (
        "http://schemas.openxmlformats.org/package/2006/relationships"
    )
    drawing_namespace = "http://schemas.openxmlformats.org/drawingml/2006/main"
    with (
        zipfile.ZipFile(io.BytesIO(source_data)) as source,
        zipfile.ZipFile(output, "w") as target,
    ):
        payloads = {
            member.filename: source.read(member) for member in source.infolist()
        }
        for name, payload in tuple(payloads.items()):
            if not name.endswith(".rels"):
                continue
            root = ElementTree.fromstring(payload)  # noqa: S314 - approved Pandoc data
            changed = False
            for node in root.findall(f"{{{relationship_namespace}}}Relationship"):
                if node.attrib.get("TargetMode") == "External":
                    node.attrib["Target"] = "document.xml"
                    node.attrib.pop("TargetMode")
                    changed = True
            if changed:
                payloads[name] = ElementTree.tostring(
                    root, encoding="utf-8", xml_declaration=True
                )
        theme_name = "word/theme/theme1.xml"
        theme = ElementTree.fromstring(  # noqa: S314 - approved Pandoc data
            payloads[theme_name]
        )
        for parent in theme.iter():
            for child in tuple(parent):
                if child.tag == f"{{{drawing_namespace}}}font":
                    parent.remove(child)
        payloads[theme_name] = ElementTree.tostring(
            theme, encoding="utf-8", xml_declaration=True
        )
        for member in source.infolist():
            target.writestr(member, payloads[member.filename])
    return output.getvalue()


def _executable(tmp_path: Path, name: str, program: str) -> Path:
    path = tmp_path / name
    path.write_text(
        "#!/usr/bin/env python3\n" + textwrap.dedent(program), encoding="utf-8"
    )
    path.chmod(0o700)
    return path


@pytest.mark.requires_pandoc
def test_pandoc_3102_sample_external_links_are_removed_from_candidate() -> None:
    with pytest.raises(TemplateValidationError) as captured:
        validate_template(
            _default_reference(), DEFAULT_REFERENCE_FONTS, LIMITS, APPROVED_FONT_POLICY
        )
    assert captured.value.code is TemplateValidationErrorCode.EXTERNAL_RELATIONSHIP
    validated = validate_template(
        _candidate_reference(), DEFAULT_REFERENCE_FONTS, LIMITS, APPROVED_FONT_POLICY
    )
    assert validated.referenced_fonts == (
        "Aptos",
        "Aptos Display",
        "Calibri",
        "Cambria",
        "Cambria Math",
        "Consolas",
        "Courier New",
        "Times New Roman",
    )


@pytest.mark.requires_libreoffice
def test_real_libreoffice_version_is_exactly_approved() -> None:
    completed = subprocess.run(
        ["soffice", "--version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert completed.stdout.splitlines()[0].startswith("LibreOffice 26.2.5.2 ")


@pytest.mark.requires_pandoc
@pytest.mark.requires_libreoffice
def test_real_blank_pandoc_and_libreoffice_opening_precede_activation(
    tmp_path: Path,
) -> None:
    context = TemplateActivationContext(
        LIMITS,
        APPROVED_FONT_POLICY,
        TemplateEngineConfig("pandoc", "soffice", 30.0, 2.0, tmp_path),
        os.environ,
    )
    validated = validate_template_for_activation(
        _candidate_reference(), DEFAULT_REFERENCE_FONTS, context
    )
    assert validated.resolved_fonts == (
        ("Aptos", "Carlito"),
        ("Aptos Display", "Carlito"),
        ("Calibri", "Carlito"),
        ("Cambria", "Caladea"),
        ("Cambria Math", "DejaVu Serif"),
        ("Consolas", "Liberation Mono"),
        ("Courier New", "Liberation Mono"),
        ("Times New Roman", "Liberation Serif"),
    )


@pytest.mark.parametrize(
    ("requested", "expected"),
    (
        ("Arial", "Liberation Sans"),
        ("Times New Roman", "Liberation Serif"),
        ("Courier New", "Liberation Mono"),
        ("Calibri", "Carlito"),
        ("Cambria", "Caladea"),
        ("sans-serif", "Liberation Sans"),
        ("serif", "Liberation Serif"),
        ("monospace", "Liberation Mono"),
    ),
)
@pytest.mark.requires_libreoffice
def test_real_fontconfig_substitution_order(requested: str, expected: str) -> None:
    completed = subprocess.run(
        ["fc-match", "--sort", "--format=%{family}\n", requested],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert completed.stdout.splitlines()[0].split(",", maxsplit=1)[0] == expected


@pytest.mark.requires_libreoffice
def test_fontconfig_inventory_is_exact_and_contains_no_noto() -> None:
    completed = subprocess.run(
        ["fc-list", "--format=%{file}|%{family}|%{style}\n"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    lines = completed.stdout.splitlines()
    assert len(lines) == 32
    assert all(line.startswith("/opt/md-converter/fonts/") for line in lines)
    assert not any("noto" in line.casefold() for line in lines)
    for family in (
        "Liberation Sans",
        "Liberation Serif",
        "Liberation Mono",
        "Carlito",
        "Caladea",
        "DejaVu Sans",
        "DejaVu Serif",
        "DejaVu Sans Mono",
    ):
        family_lines = [line for line in lines if f"|{family}|" in line]
        assert len(family_lines) == 4


@pytest.mark.requires_libreoffice
def test_installed_font_files_and_notices_match_the_pinned_manifest() -> None:
    manifest = json.loads(
        Path("/opt/toolchain/evidence/font-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert manifest["scripts"] == ["Greek", "Latin"]
    assert manifest["noto_families"] == []
    installed: set[str] = set()
    for artifact in manifest["artifacts"]:
        assert artifact["signature"] in {
            "none-published",
            "github-verified-commit",
            "publisher-sha256",
        }
        notice = Path("/usr/share/licenses/md-converter-fonts") / artifact["notice"]
        assert (
            hashlib.sha256(notice.read_bytes()).hexdigest() == artifact["notice_sha256"]
        )
        for name, expected in artifact["files"].items():
            font = Path("/opt/md-converter/fonts") / name
            assert hashlib.sha256(font.read_bytes()).hexdigest() == expected
            installed.add(name)
    assert len(installed) == 32
    assert installed == {
        Path(line.split("|", maxsplit=1)[0]).name
        for line in subprocess.run(
            ["fc-list", "--format=%{file}|%{family}\n"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ).stdout.splitlines()
    }


@pytest.mark.requires_pandoc
@pytest.mark.parametrize(
    ("pandoc_program", "libreoffice_program", "expected"),
    (
        (
            "raise SystemExit(7)",
            "raise SystemExit(0)",
            TemplateValidationErrorCode.ENGINE_FAILURE,
        ),
        (
            "import time; time.sleep(30)",
            "raise SystemExit(0)",
            TemplateValidationErrorCode.ENGINE_TIMEOUT,
        ),
        (
            """
            import shutil, sys
            source = next(value.split("=", 1)[1] for value in sys.argv if value.startswith("--reference-doc="))
            output = next(value.split("=", 1)[1] for value in sys.argv if value.startswith("--output="))
            shutil.copyfile(source, output)
            """,
            "raise SystemExit(0)",
            TemplateValidationErrorCode.ENGINE_FAILURE,
        ),
    ),
)
def test_engine_boundaries_normalize_failure_timeout_and_missing_output(
    tmp_path: Path,
    pandoc_program: str,
    libreoffice_program: str,
    expected: TemplateValidationErrorCode,
) -> None:
    fixture_root = (
        Path(os.environ.get("ENGINE_FIXTURE_ROOT", str(tmp_path))) / tmp_path.name
    )
    fixture_root.mkdir(parents=True, exist_ok=True)
    pandoc = _executable(fixture_root, "pandoc-fixture", pandoc_program)
    libreoffice = _executable(fixture_root, "libreoffice-fixture", libreoffice_program)
    context = TemplateActivationContext(
        LIMITS,
        APPROVED_FONT_POLICY,
        TemplateEngineConfig(str(pandoc), str(libreoffice), 0.1, 0.2, tmp_path),
        os.environ,
    )
    with pytest.raises(TemplateValidationError) as captured:
        validate_template_for_activation(
            _candidate_reference(), DEFAULT_REFERENCE_FONTS, context
        )
    assert captured.value.code is expected


@pytest.mark.requires_pandoc
def test_timeout_kills_a_descendant_that_ignores_sigterm(tmp_path: Path) -> None:
    fixture_root = (
        Path(os.environ.get("ENGINE_FIXTURE_ROOT", str(tmp_path))) / tmp_path.name
    )
    fixture_root.mkdir(parents=True, exist_ok=True)
    pid_file = fixture_root / "descendant.pid"
    ready_file = fixture_root / "descendant.ready"
    child_program = (
        "import signal,time,pathlib; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"pathlib.Path({str(ready_file)!r}).write_text('ready'); "
        "time.sleep(30)"
    )
    engine = _executable(
        fixture_root,
        "descendant-engine",
        f"""
        import pathlib, subprocess, sys, time
        child = subprocess.Popen([sys.executable, "-c", {child_program!r}])
        ready = pathlib.Path({str(ready_file)!r})
        while not ready.exists():
            time.sleep(0.005)
        pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))
        time.sleep(30)
        """,
    )
    config = TemplateEngineConfig(str(engine), "unused", 1.0, 0.2, tmp_path)
    with pytest.raises(TemplateValidationError) as captured:
        template_engines._run((str(engine),), tmp_path, os.environ, config)
    assert captured.value.code is TemplateValidationErrorCode.ENGINE_TIMEOUT
    descendant_pid = int(pid_file.read_text())
    deadline = time.monotonic() + 2.0

    def descendant_is_running() -> bool:
        try:
            state = Path(f"/proc/{descendant_pid}/stat").read_text().split()[2]
        except FileNotFoundError:
            return False
        # Pytest is PID 1 in this isolated test container, so a killed orphan can
        # remain as a zombie until container exit. A zombie cannot execute code.
        return state != "Z"

    try:
        while time.monotonic() < deadline:
            if not descendant_is_running():
                break
            time.sleep(0.01)
        else:
            pytest.fail("Timed-out engine descendant survived process-group cleanup")
    finally:
        if descendant_is_running():
            with suppress(ProcessLookupError):
                os.kill(descendant_pid, signal.SIGKILL)


@pytest.mark.requires_pandoc
def test_unavailable_engine_is_content_free(tmp_path: Path) -> None:
    context = TemplateActivationContext(
        LIMITS,
        APPROVED_FONT_POLICY,
        TemplateEngineConfig(
            str(tmp_path / "absent-pandoc"), "unused-soffice", 1.0, 0.2, tmp_path
        ),
        os.environ,
    )
    with pytest.raises(TemplateValidationError) as captured:
        validate_template_for_activation(
            _candidate_reference(), DEFAULT_REFERENCE_FONTS, context
        )
    assert captured.value.code is TemplateValidationErrorCode.ENGINE_UNAVAILABLE
    assert str(tmp_path) not in str(captured.value)


@pytest.mark.requires_pandoc
@pytest.mark.requires_libreoffice
def test_versioned_api_service_publishes_only_after_real_engine_activation(
    tmp_path: Path,
) -> None:
    context = TemplateActivationContext(
        LIMITS,
        APPROVED_FONT_POLICY,
        TemplateEngineConfig("pandoc", "soffice", 30.0, 2.0, tmp_path),
        os.environ,
    )
    service, _catalog, owner, _engine = _versioned_service(tmp_path, context)

    template, version = service.create_versioned(
        owner,
        TemplateCreate(uuid4(), "Real activation", "T15 boundary"),
        _candidate_reference(),
        DEFAULT_REFERENCE_FONTS.families,
    )

    assert template.current_version_id == version.id
    assert version.declared_fonts == DEFAULT_REFERENCE_FONTS.families
    assert version.validation_trace == (
        "static_ooxml",
        "pandoc_blank_conversion",
        "libreoffice_open_save",
    )
    assert service.download(owner, template.id)[2] == _candidate_reference()


@pytest.mark.parametrize("failed_engine", ["pandoc", "libreoffice"])
@pytest.mark.requires_pandoc
@pytest.mark.requires_libreoffice
def test_real_activation_engine_failure_never_publishes_template_or_object(
    tmp_path: Path, failed_engine: str
) -> None:
    engines = {
        "pandoc": "/bin/false" if failed_engine == "pandoc" else "pandoc",
        "libreoffice": ("/bin/false" if failed_engine == "libreoffice" else "soffice"),
    }
    context = TemplateActivationContext(
        LIMITS,
        APPROVED_FONT_POLICY,
        TemplateEngineConfig(
            engines["pandoc"], engines["libreoffice"], 30.0, 2.0, tmp_path
        ),
        os.environ,
    )
    service, catalog, owner, engine = _versioned_service(tmp_path, context)

    with pytest.raises(TemplateValidationError):
        service.create_versioned(
            owner,
            TemplateCreate(uuid4(), "Rejected", "Engine failure"),
            _candidate_reference(),
            DEFAULT_REFERENCE_FONTS.families,
        )

    assert (
        catalog.search(
            TemplateSearch(), viewer_id=owner.id, viewer_is_admin=False
        ).total
        == 0
    )
    with Session(engine) as database:
        assert (
            database.scalar(select(func.count()).select_from(TemplateVersionRow)) == 0
        )
    object_root = tmp_path / "objects" / "template-versions"
    assert not object_root.exists() or not any(object_root.rglob("*"))
