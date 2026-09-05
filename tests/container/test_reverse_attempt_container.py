"""Packaging contract for the isolated T70 reverse-attempt image."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import tomllib
from pathlib import Path

import pytest

from markweave.reversions._anydoc_compat import UPSTREAM_RENDERER_SURFACES
from scripts.container import integrity, verify_supply_chain
from scripts.container.verify_anydoc_cargo_sbom import (
    EXPECTED_COMPONENT_COUNT,
    EXPECTED_DEPENDENCY_COUNT,
    EXPECTED_SHA256,
    verify_anydoc_cargo_sbom,
)

ROOT = Path(__file__).parents[2]
CONTAINERFILE = ROOT / "containers/reverse-attempt/Containerfile"


@pytest.mark.unit
def test_reverse_attempt_dependency_is_exact_and_excluded_from_application_all() -> (
    None
):
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]

    assert extras["reverse-attempt"] == [
        "cairosvg>=2.8,<3",
        "defusedxml>=0.7,<1",
        "firecrawl-anydoc==0.2.4",
        "pillow>=12,<13",
        "tinycss2>=1.5,<2",
    ]
    assert extras["all"] == ["markweave[standalone,distributed]"]
    assert "reverse-attempt" not in extras["all"][0]


@pytest.mark.unit
def test_reverse_attempt_lock_pins_reviewed_linux_wheel() -> None:
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    evidence = json.loads(
        (ROOT / "spikes/anydoc/supply-chain.json").read_text(encoding="utf-8")
    )
    wheel = evidence["linux_x86_64_wheel"]

    assert 'name = "firecrawl-anydoc"' in lock
    assert 'version = "0.2.4"' in lock
    assert wheel["filename"] in lock
    assert f'hash = "sha256:{wheel["sha256"]}"' in lock


@pytest.mark.unit
def test_reverse_attempt_image_has_fixed_minimal_runtime_contract() -> None:
    containerfile = CONTAINERFILE.read_text(encoding="utf-8")

    assert "ubi9/python-314@sha256:" in containerfile
    assert (
        "uv sync --locked --no-dev --no-editable --extra reverse-attempt"
        in containerfile
    )
    assert "from markweave.conversion.images import normalize_image" in containerfile
    assert "COPY src ./src" not in containerfile
    assert "src/markweave/version.py" in containerfile
    assert "COPY src/markweave/reversions ./src/markweave/reversions" in containerfile
    for shared_module in ("__init__.py", "errors.py", "images.py"):
        assert f"src/markweave/conversion/{shared_module}" in containerfile
    assert (
        'ENTRYPOINT ["python", "-m", "markweave.reversions.attempt_main"]'
        in containerfile
    )
    assert "CMD " not in containerfile
    assert "EXPOSE " not in containerfile
    assert "RAYON_NUM_THREADS=1" in containerfile
    assert "USER 1001:0" in containerfile
    assert "spikes/anydoc/LICENSE.anydoc" in containerfile
    assert "/opt/markweave/licenses/markweave/LICENSE" in containerfile
    assert "src/markweave/reversions/ANYDOC_COMPAT_LICENSE.txt" in containerfile
    assert "/usr/lib/node_modules/npm" in containerfile
    assert "MARKWEAVE_REVERSE_MAX_INPUT_BYTES" not in containerfile
    assert "MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES" not in containerfile
    assert "dnf install" not in containerfile
    assert "CAIRO_NEVRA=cairo-1.17.4-7.el9.x86_64" in containerfile
    assert "ANYDOC_CARGO_SBOM_SHA256=" in containerfile
    assert "/opt/markweave/sbom/anydoc-cargo.cdx.json" in containerfile

    forbidden = (
        "--extra all",
        "pandoc-",
        "google-chrome",
        "libreoffice",
        "mmdc",
        "puppeteer",
    )
    assert all(value not in containerfile.lower() for value in forbidden)


@pytest.mark.unit
def test_workspace_overlay_fails_closed_on_incompatible_base_image() -> None:
    fixture = (
        ROOT / "tests/integration/broker/fixtures/WorkspaceContainerfile"
    ).read_text(encoding="utf-8")

    preflight = fixture.index("RUN test -x /opt/markweave/venv/bin/python")
    overlay = fixture.index("COPY src/markweave/reversions")
    assert preflight < overlay
    assert "env -i /opt/markweave/venv/bin/python -E -c" in fixture
    assert "assert " not in fixture
    for contract in (
        "sys.version_info[:2] == (3, 14)",
        'pathlib.Path("/opt/markweave/venv/bin/python").resolve()',
        'pathlib.Path("/opt/markweave/venv").resolve()',
        'pathlib.Path("/opt/markweave/venv/lib/python3.14/site-packages")',
        "str(destination) in sys.path",
        'find_spec("markweave.reversions.attempt_main")',
        "specification.origin is not None",
        'pathlib.Path(specification.origin).resolve().parent == destination / "markweave/reversions"',
        "raise SystemExit(0 if valid else 1)",
    ):
        assert contract in fixture


@pytest.mark.unit
def test_reverse_attempt_smoke_enforces_runtime_separation() -> None:
    smoke = (ROOT / "scripts/container/smoke-reverse-attempt.sh").read_text(
        encoding="utf-8"
    )

    for value in (
        "--network none",
        "--read-only",
        "--cap-drop all",
        "--security-opt no-new-privileges",
        "--user 12345:0",
        "--tmpfs /work:",
    ):
        assert value in smoke
    for forbidden_module in ("fastapi", "uvicorn"):
        assert f'find_spec("{forbidden_module}") is None' in smoke
    for value in (
        "MARKWEAVE_REVERSE_MAX_INPUT_BYTES=1000000",
        "MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES=2000000",
        '"$workspace:/work:rw"',
        'test ! -s "$stdout_file"',
        'test ! -s "$stderr_file"',
        "decode_response_metadata",
        'Path("spikes/anydoc/corpus/docx/text.docx")',
        'podman unshare chown -R 12345:0 -- "$workspace"',
        'podman unshare grep -q \'"state":"complete"\'',
        'PurePosixPath("safe.svg")',
        "MARKDOWN_WITH_ASSETS",
        '"assets/image-0001.png"',
    ):
        assert value in smoke

    run_ci = (ROOT / "scripts/container/run-ci.sh").read_text(encoding="utf-8")
    assert 'build-reverse-attempt.sh "$reverse_attempt_image"' in run_ci
    assert 'smoke-reverse-attempt.sh "$reverse_attempt_image"' in run_ci
    assert "tests/integration/broker/test_podman_runtime_integration.py" in run_ci
    assert 'MARKWEAVE_T70_PODMAN_TEST_IMAGE="$reverse_attempt_image"' in run_ci
    assert '"$reverse_attempt_image" artifacts/reverse-attempt' in run_ci
    assert "ci reverse-attempt" in run_ci

    supply_chain = (ROOT / "scripts/container/supply-chain.sh").read_text(
        encoding="utf-8"
    )
    for value in (
        "scripts.container.verify_anydoc_cargo_sbom",
        "anydoc-cargo.cdx.json",
        "anydoc-cargo-vulnerabilities.json",
        '--profile "$evidence_profile"',
    ):
        assert value in supply_chain

    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    for artifact in (
        "artifacts/reverse-attempt/sbom.cdx.json",
        "artifacts/reverse-attempt/sbom.spdx.json",
        "artifacts/reverse-attempt/vulnerabilities.json",
        "artifacts/reverse-attempt/image-metadata.json",
        "artifacts/reverse-attempt/image.oci.tar",
        "artifacts/reverse-attempt/anydoc-cargo.cdx.json",
        "artifacts/reverse-attempt/anydoc-cargo-vulnerabilities.json",
        "artifacts/reverse-attempt/release-bundle.sha256",
    ):
        assert artifact in workflow


@pytest.mark.unit
def test_pinned_anydoc_wheel_carries_the_reviewed_cargo_inventory() -> None:
    distribution = importlib.metadata.distribution("firecrawl-anydoc")
    candidates = [
        path
        for path in distribution.files or ()
        if str(path).endswith("sboms/anydoc-python.cyclonedx.json")
    ]

    assert len(candidates) == 1
    sbom = Path(str(distribution.locate_file(candidates[0])))
    verify_anydoc_cargo_sbom(sbom)
    document = json.loads(sbom.read_text(encoding="utf-8"))
    assert len(document["components"]) == EXPECTED_COMPONENT_COUNT
    assert all(component["licenses"] for component in document["components"])


@pytest.mark.unit
def test_reverse_attempt_evidence_profile_binds_the_cargo_scan(tmp_path: Path) -> None:
    source_names = verify_supply_chain.REVERSE_ATTEMPT_FILES - {"image-metadata.json"}
    for name in source_names:
        (tmp_path / name).write_bytes(f"content for {name}".encode())
    source_digests = {
        name: integrity.sha256_file(tmp_path / name) for name in source_names
    }
    (tmp_path / "image-metadata.json").write_text(
        json.dumps({"artifacts": source_digests}), encoding="utf-8"
    )

    expected = verify_supply_chain.create_manifest(
        tmp_path, expected_files=verify_supply_chain.REVERSE_ATTEMPT_FILES
    )

    verify_supply_chain.verify_bundle(
        tmp_path,
        expected_manifest_sha256=expected,
        expected_files=verify_supply_chain.REVERSE_ATTEMPT_FILES,
    )


@pytest.mark.unit
def test_supply_chain_inventory_tracks_exact_compatibility_surface() -> None:
    inventory = json.loads(
        (ROOT / "docs/evidence/t70-reverse-attempt-supply-chain.json").read_text(
            encoding="utf-8"
        )
    )

    assert inventory["compatibility_adapter"][
        "mirrored_upstream_renderer_behaviors"
    ] == list(UPSTREAM_RENDERER_SURFACES)
    assert inventory["compatibility_adapter"]["license_path"] == (
        "src/markweave/reversions/ANYDOC_COMPAT_LICENSE.txt"
    )
    license_path = ROOT / inventory["compatibility_adapter"]["license_path"]
    assert (
        hashlib.sha256(license_path.read_bytes()).hexdigest()
        == inventory["compatibility_adapter"]["license_sha256"]
    )
    cargo = inventory["anydoc"]["embedded_cargo_sbom"]
    assert cargo["sha256"] == EXPECTED_SHA256
    assert cargo["component_count"] == EXPECTED_COMPONENT_COUNT
    assert cargo["dependency_node_count"] == EXPECTED_DEPENDENCY_COUNT
    assert cargo["components_without_license_claim"] == 0
