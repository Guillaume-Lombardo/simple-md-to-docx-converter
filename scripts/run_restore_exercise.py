"""Run an operator-supplied isolated restore command and retain its measured report."""

from __future__ import annotations

import argparse
import subprocess
from datetime import datetime
from pathlib import Path

from markweave.config import StorageProfile
from markweave.recovery import FilesystemRestoreReportStore, RestoreExerciseRunner


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, choices=tuple(StorageProfile))
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--evidence-id", required=True)
    parser.add_argument("--backup-created-at", required=True)
    parser.add_argument("--report-directory", required=True, type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = arguments.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("an isolated restore-and-readiness command is required after --")

    def restore(timeout_seconds: float) -> bool:
        result = subprocess.run(  # noqa: S603 - fixed operator command, never upload data
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
        )
        return result.returncode == 0

    report = RestoreExerciseRunner(
        FilesystemRestoreReportStore(arguments.report_directory)
    ).run(
        StorageProfile(arguments.profile),
        backup_id=arguments.backup_id,
        evidence_id=arguments.evidence_id,
        backup_created_at=datetime.fromisoformat(arguments.backup_created_at),
        restore=restore,
    )
    return 0 if report.targets_met else 1


if __name__ == "__main__":
    raise SystemExit(main())
