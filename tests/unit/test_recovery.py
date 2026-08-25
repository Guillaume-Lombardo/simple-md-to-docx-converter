"""Recovery-target measurement and immutable-report tests."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from markweave.config import StorageProfile
from markweave.recovery import (
    FilesystemRestoreReportStore,
    RestoreExerciseRunner,
)


@pytest.mark.unit
def test_standalone_restore_exercise_meets_targets_and_retains_report(
    tmp_path: Path,
) -> None:
    started = datetime(2026, 8, 24, 8, tzinfo=UTC)
    times = iter((started, started + timedelta(hours=3)))
    report = RestoreExerciseRunner(
        FilesystemRestoreReportStore(tmp_path),
        clock=lambda: next(times),
        monotonic_clock=iter((10.0, 10_810.0)).__next__,
        new_id=lambda: UUID(int=1),
    ).run(
        StorageProfile.STANDALONE,
        backup_id="backup-42",
        evidence_id="ready-check-42",
        backup_created_at=started - timedelta(hours=23),
        restore=lambda timeout: timeout == 4 * 60 * 60,
    )
    assert report.targets_met
    payload = json.loads((tmp_path / f"{report.id}.json").read_text())
    assert payload["target_rpo_seconds"] == 24 * 60 * 60
    assert payload["target_rto_seconds"] == 4 * 60 * 60


@pytest.mark.unit
def test_distributed_restore_failure_is_reported_and_report_is_immutable(
    tmp_path: Path,
) -> None:
    started = datetime(2026, 8, 24, 8, tzinfo=UTC)
    times = iter((started, started + timedelta(minutes=30)))
    runner = RestoreExerciseRunner(
        FilesystemRestoreReportStore(tmp_path),
        clock=lambda: next(times),
        monotonic_clock=iter((20.0, 1_820.0)).__next__,
        new_id=lambda: UUID(int=2),
    )
    report = runner.run(
        StorageProfile.DISTRIBUTED,
        backup_id="backup-43",
        evidence_id="ready-check-43",
        backup_created_at=started - timedelta(minutes=30),
        restore=lambda timeout: timeout == 0,
    )
    assert not report.targets_met
    assert report.observed_rto_seconds == 30 * 60
    with pytest.raises(RuntimeError, match="could not be retained"):
        FilesystemRestoreReportStore(tmp_path).save(report)


@pytest.mark.unit
def test_restore_exercise_rejects_invalid_identifiers_and_timestamps(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    runner = RestoreExerciseRunner(
        FilesystemRestoreReportStore(tmp_path), clock=lambda: now
    )
    with pytest.raises(ValueError, match="identifiers"):
        runner.run(
            StorageProfile.STANDALONE,
            backup_id=" ",
            evidence_id="proof",
            backup_created_at=now,
            restore=lambda _timeout: True,
        )
    with pytest.raises(ValueError, match="future"):
        runner.run(
            StorageProfile.STANDALONE,
            backup_id="backup",
            evidence_id="proof",
            backup_created_at=now + timedelta(seconds=1),
            restore=lambda _timeout: True,
        )
    with pytest.raises(ValueError, match="timezone"):
        runner.run(
            StorageProfile.STANDALONE,
            backup_id="backup",
            evidence_id="proof",
            backup_created_at=datetime(2026, 8, 24),  # noqa: DTZ001
            restore=lambda _timeout: True,
        )


@pytest.mark.unit
def test_restore_rto_uses_monotonic_elapsed_time_during_wall_clock_rollback(
    tmp_path: Path,
) -> None:
    started = datetime(2026, 8, 24, 8, tzinfo=UTC)
    wall_times = iter((started, started - timedelta(hours=2)))
    monotonic_times = iter((100.0, 100.0 + 45 * 60))

    report = RestoreExerciseRunner(
        FilesystemRestoreReportStore(tmp_path),
        clock=lambda: next(wall_times),
        monotonic_clock=lambda: next(monotonic_times),
    ).run(
        StorageProfile.DISTRIBUTED,
        backup_id="backup-wall-rollback",
        evidence_id="ready-wall-rollback",
        backup_created_at=started - timedelta(minutes=30),
        restore=lambda _timeout: True,
    )

    assert report.completed_at < report.started_at
    assert report.observed_rto_seconds == 45 * 60
    assert report.targets_met


@pytest.mark.unit
def test_restore_rejects_a_broken_monotonic_clock(tmp_path: Path) -> None:
    now = datetime(2026, 8, 24, tzinfo=UTC)
    monotonic_times = iter((2.0, 1.0))
    runner = RestoreExerciseRunner(
        FilesystemRestoreReportStore(tmp_path),
        clock=lambda: now,
        monotonic_clock=lambda: next(monotonic_times),
    )

    with pytest.raises(RuntimeError, match="monotonic clock"):
        runner.run(
            StorageProfile.STANDALONE,
            backup_id="backup",
            evidence_id="proof",
            backup_created_at=now,
            restore=lambda _timeout: True,
        )
    assert not tuple(tmp_path.iterdir())
