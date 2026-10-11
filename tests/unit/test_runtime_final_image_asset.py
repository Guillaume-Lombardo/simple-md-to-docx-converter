"""Static wiring checks for the T36 final-image runtime CLI smoke."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_runtime_cli_final_image_smoke_is_wired_and_hardened() -> None:
    script_path = Path("tests/e2e/runtime-operations-final-image.sh")
    subprocess.run(("bash", "-n", str(script_path)), check=True)
    script = script_path.read_text(encoding="utf-8")
    runner = Path("scripts/container/run-ci.sh").read_text(encoding="utf-8")

    assert 'bash tests/e2e/runtime-operations-final-image.sh "$final_image"' in runner
    for contract in (
        "--user",
        "--read-only",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--network=none",
        "--pids-limit=256",
        "--tmpfs /work:",
        "--tmpfs /data:",
    ):
        assert contract in script
    assert "markweave --json migrate" in script
    assert "markweave --json --timeout 15 doctor" in script
    assert "markweave worker" in script


def test_local_conversion_final_image_e2e_is_wired_and_hardened() -> None:
    script_path = Path("tests/e2e/local-conversion-final-image.sh")
    subprocess.run(("bash", "-n", str(script_path)), check=True)
    script = script_path.read_text(encoding="utf-8")
    runner = Path("scripts/container/run-ci.sh").read_text(encoding="utf-8")

    assert 'bash tests/e2e/local-conversion-final-image.sh "$final_image"' in runner
    assert runner.index("runtime-operations-final-image.sh") < runner.index(
        "local-conversion-final-image.sh"
    )
    for contract in (
        "--user",
        "--read-only",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        '--security-opt="seccomp=$seccomp_profile"',
        "--network=none",
        "--pids-limit=256",
        "--tmpfs /work:",
        "--env-file",
        ":ro,Z",
    ):
        assert contract in script
    for scenario in (
        "--require-pinned",
        "engine_missing",
        "validation",
        "engine_version_unpinned",
        "conversion_timeout",
        "conversion_cancelled",
        "output_write_failed",
        "--overwrite",
        "flock --exclusive",
        "pause-after-first-replacement",
        "pause-before-restore",
        "report-lock-wait",
        "/work/output/document.pdf:ro,Z",
        "podman stop",
        "mermaid-cli",
    ):
        assert scenario in script
    assert "--entrypoint /bin/bash" not in script


def test_publication_sync_hook_is_test_only_and_never_packaged() -> None:
    hook = Path("tests/e2e/fixtures/local-publication-sync/sitecustomize.py")
    script = Path("tests/e2e/local-conversion-final-image.sh").read_text(
        encoding="utf-8"
    )
    assert hook.is_file()
    assert '"$sync_hook:/work/hooks:ro,Z"' in script
    for path in (Path("Containerfile"), Path("pyproject.toml")):
        assert "local-publication-sync" not in path.read_text(encoding="utf-8")
    assert not any(Path("src").rglob("sitecustomize.py"))
