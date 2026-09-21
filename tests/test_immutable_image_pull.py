"""Policy tests for bounded immutable container-image acquisition."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
PULLER = ROOT / "scripts/ci/pull-immutable-image.sh"
IMAGE = "registry.example.test/markweave@sha256:" + "a" * 64
DIGEST = "sha256:" + "a" * 64


def _fake_podman(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(parents=True)
    call_log = tmp_path / "podman.log"
    podman = fake_bin / "podman"
    podman.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "%s\\n" "$*" >>"$FAKE_PODMAN_LOG"\n'
        'if [[ "$1" == pull ]]; then\n'
        '  count_file="${FAKE_PODMAN_LOG}.count"\n'
        '  count=0; [[ -f "$count_file" ]] && count="$(cat "$count_file")"\n'
        '  count=$((count + 1)); printf "%s" "$count" >"$count_file"\n'
        '  case "$FAKE_PULL_MODE" in\n'
        '    transient-then-success) [[ "$count" -eq 1 ]] && { printf "%s\\n" \'unexpected EOF (while reconnecting: Get "https://cdn.example.test/layer?X-Amz-Signature=not-a-secret&akamai_signature=not-a-secret")\' >&2; exit 125; } ;;\n'
        '    transient) echo "unexpected EOF" >&2; exit 125 ;;\n'
        '    permanent) printf "%s\\n" \'unexpected EOF (while reconnecting: Get "https://cdn.example.test/layer?X-Amz-Signature=not-a-secret")\' "manifest unknown" >&2; exit 125 ;;\n'
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        '[[ "$1" == image && "$2" == inspect ]]\n'
        'printf "%s\\n" "${FAKE_INSPECT_DIGEST:-' + DIGEST + '}"\n',
        encoding="utf-8",
    )
    podman.chmod(0o755)
    sleep = fake_bin / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o755)
    return fake_bin, call_log


def _run_puller(
    tmp_path: Path, mode: str, *, digest: str = DIGEST
) -> subprocess.CompletedProcess[str]:
    fake_bin, call_log = _fake_podman(tmp_path)
    environment = os.environ | {
        "FAKE_INSPECT_DIGEST": digest,
        "FAKE_PODMAN_LOG": str(call_log),
        "FAKE_PULL_MODE": mode,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }
    return subprocess.run(
        [str(PULLER), IMAGE, DIGEST],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


@pytest.mark.parametrize(
    "arguments",
    (
        (),
        ("registry.example.test/markweave:latest", DIGEST),
        (IMAGE, "sha256:" + "b" * 64),
    ),
)
def test_puller_rejects_nonmatching_immutable_inputs_before_podman(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    fake_bin, call_log = _fake_podman(tmp_path)

    result = subprocess.run(
        [str(PULLER), *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ | {"PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )

    assert result.returncode == 2
    assert not call_log.exists()


def test_puller_retries_a_transient_transport_failure_then_verifies_digest(
    tmp_path: Path,
) -> None:
    result = _run_puller(tmp_path, "transient-then-success")

    assert result.returncode == 0
    assert "attempt 1/3 failed with a transient transport error" in result.stderr
    assert "X-Amz-Signature" not in result.stderr
    assert f"verified digest {DIGEST}" in result.stdout
    calls = (tmp_path / "podman.log").read_text(encoding="utf-8").splitlines()
    assert sum(call.startswith("pull ") for call in calls) == 2
    assert sum(call.startswith("image inspect ") for call in calls) == 1


def test_puller_fails_deterministically_after_bounded_transient_retries(
    tmp_path: Path,
) -> None:
    result = _run_puller(tmp_path, "transient")

    assert result.returncode == 1
    assert "exhausted 3 transient transport attempts" in result.stderr
    calls = (tmp_path / "podman.log").read_text(encoding="utf-8").splitlines()
    assert sum(call.startswith("pull ") for call in calls) == 3


def test_puller_does_not_retry_permanent_or_integrity_failures(tmp_path: Path) -> None:
    permanent = _run_puller(tmp_path / "permanent", "permanent")
    integrity = _run_puller(
        tmp_path / "integrity", "success", digest="sha256:" + "b" * 64
    )

    assert permanent.returncode == 1
    assert "failed without retry" in permanent.stderr
    assert integrity.returncode == 1
    assert "digest verification failed" in integrity.stderr
    for name in ("permanent", "integrity"):
        calls = (
            (tmp_path / name / "podman.log").read_text(encoding="utf-8").splitlines()
        )
        assert sum(call.startswith("pull ") for call in calls) == 1
