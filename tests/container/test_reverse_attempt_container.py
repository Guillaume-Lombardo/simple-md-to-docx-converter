"""Packaging contract for the isolated T70 reverse-attempt image."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

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
        'importlib.import_module(\\"markweave.reversions.attempt_main\\")',
        "except BaseException:",
        "return None",
        'getattr(attempt_main, "__file__", None)',
        "origin is not None",
        'pathlib.Path(origin).resolve().parent == destination / "markweave/reversions"',
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
    assert "uv run pytest tests/integration/broker" in run_ci
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


@pytest.mark.unit
def test_release_retains_and_attests_the_matched_three_image_set() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/container-release.yml").read_text()
    )
    jobs = workflow["jobs"]
    build = jobs["build-and-publish"]["steps"]
    runs = "\n".join(step.get("run", "") for step in build)
    assert runs.count('build-reverse-attempt.sh "$reverse_attempt_image"') == 1
    assert runs.count('smoke-reverse-attempt.sh "$reverse_attempt_image"') == 1
    assert "artifacts/container/reverse_attempt release reverse-attempt" in runs
    assert (
        'MARKWEAVE_E2E_LOCAL_REVERSE_ATTEMPT_IMAGE="localhost/md-converter-reverse-attempt:$RELEASE_VERSION"'
        in runs
    )
    retained = next(
        index
        for index, step in enumerate(build)
        if step["name"] == "Retain staged pair before registry mutation"
    )
    published = next(
        index for index, step in enumerate(build) if step.get("id") == "push"
    )
    assert retained < published
    assert build[retained]["with"]["path"].splitlines() == [
        "artifacts/container/backend/",
        "artifacts/container/frontend/",
        "artifacts/container/reverse_attempt/",
    ]
    for job in ("build-and-publish", "recover-evidence"):
        assert (
            jobs[job]["outputs"]["reverse-attempt-digest"]
            == "${{ steps.push.outputs.reverse-attempt-digest }}"
        )
    attestations = [
        step["with"]["subject-name"]
        for step in jobs["attest"]["steps"]
        if "subject-name" in step.get("with", {})
    ]
    assert attestations == [
        "ghcr.io/guillaume-lombardo/md-converter",
        "ghcr.io/guillaume-lombardo/md-converter-web",
        "ghcr.io/guillaume-lombardo/md-converter-reverse-attempt",
    ]


@pytest.mark.integration
@pytest.mark.parametrize("legacy", [False, True])
def test_recovery_attaches_only_the_source_selected_image_roles(
    tmp_path: Path, legacy: bool
) -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/container-release.yml").read_text()
    )
    jobs = workflow["jobs"]
    reverse_attestation = next(
        step
        for step in jobs["attest"]["steps"]
        if step["name"] == "Attest the published reverse-attempt image identity"
    )
    assert (
        reverse_attestation["if"]
        == "${{ needs.build-and-publish.outputs.reverse-attempt-digest != '' || needs.recover-evidence.outputs.reverse-attempt-digest != '' }}"
    )
    step = jobs["release-evidence"]["steps"][-1]
    assert (
        step["env"]["REVERSE_ATTEMPT_DIGEST"]
        == "${{ needs.build-and-publish.outputs.reverse-attempt-digest || needs.recover-evidence.outputs.reverse-attempt-digest }}"
    )
    roles = ["backend", "frontend"] + ([] if legacy else ["reverse_attempt"])
    expected = []
    for role in roles:
        directory = tmp_path / "artifacts/container" / role
        directory.mkdir(parents=True)
        names = [
            "sbom.cdx.json",
            "sbom.spdx.json",
            "vulnerabilities.json",
            "image-metadata.json",
            "registry-publication.json",
            "release-bundle.sha256",
        ]
        if role == "reverse_attempt":
            names += ["anydoc-cargo.cdx.json", "anydoc-cargo-vulnerabilities.json"]
        for name in names:
            (directory / name).write_text("fixture")
            expected.append(f"{role}-{name}")
    for name in ("release-images.json", "release-images.sha256"):
        (tmp_path / "artifacts/container" / name).write_text("fixture")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    gh = binaries / "gh"
    gh.write_text("""#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" = api ]]; then
  if [[ "$2" = *git/ref/tags* ]]; then printf '%s\\n' "$SOURCE_SHA";
  else printf '%s\\t%s\\tfalse\\tfalse\\n' "$RELEASE_TAG" "$SOURCE_SHA"; fi
else
  printf '%s\\n' "$@" > "$UPLOAD_ARGUMENTS"
fi
""")
    gh.chmod(0o700)
    env = {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "SOURCE_SHA": "a" * 40,
        "RELEASE_TAG": "v0.7.1",
        "GITHUB_REPOSITORY": "fixture/repository",
        "RUNNER_TEMP": str(tmp_path),
        "REVERSE_ATTEMPT_DIGEST": "" if legacy else "sha256:" + "b" * 64,
        "UPLOAD_ARGUMENTS": str(tmp_path / "uploaded"),
    }
    subprocess.run(
        ["bash", "-c", step["run"]],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert sorted(
        path.name for path in (tmp_path / "release-assets").iterdir()
    ) == sorted(expected)
    uploaded = (tmp_path / "uploaded").read_text()
    assert "release-images.json" in uploaded
    assert ("reverse_attempt-anydoc-cargo.cdx.json" in uploaded) is not legacy


@pytest.mark.unit
def test_final_image_measurements_reuse_the_bounded_t69_probe() -> None:
    smoke = (ROOT / "scripts/container/smoke-reverse-attempt.sh").read_text()
    measurement = smoke.split("# Reuse T69 instrumentation", 1)[1]
    assert "podman image inspect \"$requested_image\" --format '{{.Id}}'" in smoke
    for required in (
        "podman run --rm --timeout 60",
        "--network none --read-only --cap-drop all",
        "--pids-limit 16 --memory 256m --cpus 1 --user 12345:0",
        "--tmpfs /work:rw,noexec,nosuid,nodev,size=32m,mode=0770",
        '"$PWD/spikes/anydoc/probe.py:/probe.py:ro"',
        '"$PWD/spikes/anydoc/corpus:/corpus:ro"',
        '--entrypoint python "$image"',
        'runpy.run_path("/probe.py", run_name="__main__")',
        '"--iterations", "5"',
        "head -c 131073",
        '"cpu.max", "memory.max", "pids.max"',
        'report["process_inventory"]["loaded_module_names"] == []',
        'report["offline_no_ocr"]["result"] == "needs_ocr"',
        'report["environment"]["rayon_num_threads"] == "1"',
        'sample["workers"] for sample in report["concurrency"]] == [1, 2, 4]',
        '"nvidia*", "dri", "kfd", "accel*"',
        'Path("/sys/class/net")',
        'os.statvfs("/work")',
    ):
        assert required in measurement
    assert "spikes/anydoc/probe.py" not in measurement.split("uv run python", 1)[1]


@pytest.mark.integration
@pytest.mark.parametrize("case", ["valid", "oversized", "invalid-json", "existing"])
def test_measurement_receipt_is_bounded_exclusive_and_distinguishes_observations(
    tmp_path: Path,
    case: str,
) -> None:
    smoke = (ROOT / "scripts/container/smoke-reverse-attempt.sh").read_text()
    writer = smoke.split("<<'PY_RECEIPT'\n", 1)[1].split("\nPY_RECEIPT", 1)[0]
    source = tmp_path / "observations.json"
    destination = tmp_path / "receipt.json"
    source.write_text(
        json.dumps(
            {"fixture_measurement": {"cpu_ms": 1, "wall_ms": 2, "peak_rss_kib": 3}}
        )
    )
    if case == "oversized":
        source.write_bytes(b" " * 131073)
    elif case == "invalid-json":
        source.write_bytes(b"{")
    elif case == "existing":
        destination.write_bytes(b"must survive")
    image_id = "sha256:" + "a" * 64
    result = subprocess.run(
        [sys.executable, "-c", writer, str(source), str(destination), image_id],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if case != "valid":
        assert result.returncode != 0
        if case == "existing":
            assert destination.read_bytes() == b"must survive"
        else:
            assert not destination.exists()
        return
    assert result.returncode == 0, result.stderr
    report = json.loads(destination.read_bytes())
    assert destination.stat().st_mode & 0o777 == 0o600
    assert destination.stat().st_size <= 131072
    assert report["image_id"] == image_id
    assert report["measurements"] == json.loads(source.read_bytes())
    assert report["harness_containment"]["workspace_bytes"] == 33554432
    assert report["qualification"]["production_numeric_thresholds_approved"] is False
    assert (
        "do not exclude transient"
        in report["qualification"]["child_process_observation"]
    )
    assert (
        "not production worker admission"
        in report["qualification"]["concurrency_scope"]
    )
