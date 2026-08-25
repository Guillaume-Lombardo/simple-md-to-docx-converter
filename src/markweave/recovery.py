"""Automated restore-exercise policy and immutable report retention."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol
from uuid import UUID, uuid4

from markweave.config import StorageProfile


@dataclass(frozen=True, slots=True)
class RecoveryTarget:
    """Approved maximum recovery-point and recovery-time losses."""

    rpo_seconds: int
    rto_seconds: int


RECOVERY_TARGETS = {
    StorageProfile.STANDALONE: RecoveryTarget(24 * 60 * 60, 4 * 60 * 60),
    StorageProfile.DISTRIBUTED: RecoveryTarget(60 * 60, 2 * 60 * 60),
}


@dataclass(frozen=True, slots=True)
class RestoreExerciseReport:
    """Content-free retained proof from one automated restore exercise."""

    id: UUID
    profile: str
    backup_id: str
    evidence_id: str
    backup_created_at: str
    started_at: str
    completed_at: str
    observed_rpo_seconds: float
    observed_rto_seconds: float
    target_rpo_seconds: int
    target_rto_seconds: int
    restore_succeeded: bool
    targets_met: bool


class RestoreReportStore(Protocol):
    """Immutable retained-report boundary."""

    def save(self, report: RestoreExerciseReport) -> Path: ...


class FilesystemRestoreReportStore:
    """Persist reports once with owner-only permissions and directory fsync."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def save(self, report: RestoreExerciseReport) -> Path:
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = self._directory / f"{report.id}.json"
        payload = json.dumps(
            asdict(report), sort_keys=True, separators=(",", ":"), default=str
        )
        try:
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(payload)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            directory_descriptor = os.open(
                self._directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            raise RuntimeError(
                "Restore exercise report could not be retained"
            ) from None
        return target


class RestoreExerciseRunner:
    """Measure one isolated restore command and retain pass/fail evidence."""

    def __init__(
        self,
        reports: RestoreReportStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
        new_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self._reports = reports
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._new_id = new_id

    def run(
        self,
        profile: StorageProfile,
        *,
        backup_id: str,
        evidence_id: str,
        backup_created_at: datetime,
        restore: Callable[[float], bool],
    ) -> RestoreExerciseReport:
        if not backup_id.strip() or not evidence_id.strip():
            raise ValueError("Restore exercise identifiers must not be blank")
        target = RECOVERY_TARGETS[profile]
        started = self._utc(self._clock())
        backup_at = self._utc(backup_created_at)
        if backup_at > started:
            raise ValueError("Restore backup timestamp must not be in the future")
        succeeded = False
        started_monotonic = self._monotonic_clock()
        try:
            succeeded = restore(float(target.rto_seconds))
        finally:
            completed_monotonic = self._monotonic_clock()
            completed = self._utc(self._clock())
            observed_rpo = (started - backup_at).total_seconds()
            observed_rto = completed_monotonic - started_monotonic
            if observed_rto < 0:
                raise RuntimeError("Restore exercise monotonic clock moved backwards")
            report = RestoreExerciseReport(
                id=self._new_id(),
                profile=profile.value,
                backup_id=backup_id,
                evidence_id=evidence_id,
                backup_created_at=backup_at.isoformat(),
                started_at=started.isoformat(),
                completed_at=completed.isoformat(),
                observed_rpo_seconds=observed_rpo,
                observed_rto_seconds=observed_rto,
                target_rpo_seconds=target.rpo_seconds,
                target_rto_seconds=target.rto_seconds,
                restore_succeeded=succeeded,
                targets_met=(
                    succeeded
                    and observed_rpo <= target.rpo_seconds
                    and observed_rto <= target.rto_seconds
                ),
            )
            self._reports.save(report)
        return report

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Restore exercise timestamps require a timezone")
        return value.astimezone(UTC)
