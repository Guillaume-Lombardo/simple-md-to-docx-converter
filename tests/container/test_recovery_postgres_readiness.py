"""Deterministic regression coverage for recovery smoke PostgreSQL readiness."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _fake_commands(directory: Path) -> None:
    podman = directory / "podman"
    podman.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
command_name="$3"
if [[ "$command_name" == createdb ]]; then
  count=0
  if [[ -f "$FAKE_STATE" ]]; then
    count="$(<"$FAKE_STATE")"
  fi
  count=$((count + 1))
  printf '%s' "$count" >"$FAKE_STATE"
  if [[ "$FAKE_SCENARIO" == restart && "$count" -gt 1 ]]; then
    exit 0
  fi
  if [[ "$FAKE_SCENARIO" == permanent ]]; then
    echo 'createdb: permission denied' >&2
  else
    echo 'createdb: socket unavailable during restart' >&2
  fi
  exit 1
fi
if [[ "$command_name" == psql ]]; then
  if [[ "$FAKE_SCENARIO" == existing ]]; then
    echo 1
  fi
  exit 0
fi
if [[ "$command_name" == pg_isready ]]; then
  [[ "$FAKE_SCENARIO" == permanent ]]
  exit
fi
exit 2
""",
        encoding="utf-8",
    )
    podman.chmod(0o700)
    sleep = directory / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep.chmod(0o700)


def _run_readiness(tmp_path: Path, scenario: str) -> subprocess.CompletedProcess[str]:
    commands = tmp_path / "commands"
    commands.mkdir()
    _fake_commands(commands)
    environment = {
        **os.environ,
        "FAKE_SCENARIO": scenario,
        "FAKE_STATE": str(tmp_path / "attempts"),
        "PATH": f"{commands}:{os.environ['PATH']}",
    }
    return subprocess.run(
        (
            "bash",
            "-c",
            "source scripts/container/recovery-cli-smoke.sh; "
            "ensure_postgres_database postgres target",
        ),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_database_creation_retries_across_initialization_restart(
    tmp_path: Path,
) -> None:
    result = _run_readiness(tmp_path, "restart")

    assert result.returncode == 0
    assert (tmp_path / "attempts").read_text(encoding="utf-8") == "2"


def test_existing_target_database_is_idempotent(tmp_path: Path) -> None:
    result = _run_readiness(tmp_path, "existing")

    assert result.returncode == 0
    assert (tmp_path / "attempts").read_text(encoding="utf-8") == "1"


def test_permanent_createdb_failure_is_bounded_and_reported(tmp_path: Path) -> None:
    result = _run_readiness(tmp_path, "permanent")

    assert result.returncode == 1
    assert result.stderr == "createdb: permission denied\n"
    assert (tmp_path / "attempts").read_text(encoding="utf-8") == "2"


def test_invalid_database_name_fails_before_provider_call(tmp_path: Path) -> None:
    commands = tmp_path / "commands"
    commands.mkdir()
    _fake_commands(commands)
    state = tmp_path / "attempts"
    result = subprocess.run(
        (
            "bash",
            "-c",
            "source scripts/container/recovery-cli-smoke.sh; "
            "ensure_postgres_database postgres 'unsafe-name'",
        ),
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "FAKE_SCENARIO": "restart",
            "FAKE_STATE": str(state),
            "PATH": f"{commands}:{os.environ['PATH']}",
        },
    )

    assert result.returncode == 2
    assert result.stderr == "Invalid PostgreSQL database name: unsafe-name\n"
    assert not state.exists()
