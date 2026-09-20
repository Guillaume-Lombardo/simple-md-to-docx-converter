"""Atomic SQLite/PostgreSQL durable reverse-job repository."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

import markweave.persistence.reversion_jobs.reconciliation as _reconciliation
from markweave.broker.models import (
    AuthenticatedPrincipal,
    EvidenceDigest,
    TerminationProof,
)
from markweave.persistence.job_admission import (
    ACTIVE_JOB_STATES,
    lock_reverse_claim,
)
from markweave.persistence.reversion_jobs.admission import _SqlReversionAdmission
from markweave.persistence.reversion_jobs.common import (
    _attempt,
    _job,
    _trace_json,
)
from markweave.persistence.reversion_jobs.retention import _SqlReversionRetention
from markweave.persistence.schema import (
    ReversionAttemptRow,
    ReversionJobRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.reversion_jobs.errors import (
    ReversionJobConflictError,
    ReversionJobLeaseLostError,
    ReversionJobRepositoryError,
    ReversionProofRequiredError,
)
from markweave.reversion_jobs.models import (
    SHA256_CHARACTERS,
    ReversionAttempt,
    ReversionFailure,
    ReversionJob,
    ReversionJobState,
    ReversionJobStep,
    ReversionLeaseHeartbeat,
    ReversionLeaseRecoveryResult,
    ReversionTraceMetadata,
    reversion_result_object_id,
)
from markweave.reversions.models import ReverseOutputMode

_MAX_WORKER_ID_LENGTH = _reconciliation._MAX_WORKER_ID_LENGTH
_SqlReversionReconciliation = _reconciliation._SqlReversionReconciliation


class SqlReversionJobRepository(
    _SqlReversionAdmission, _SqlReversionRetention, _SqlReversionReconciliation
):
    """Complete reverse queue contract with owner-bound public reads."""

    def claim(
        self,
        worker_id: str,
        principal: AuthenticatedPrincipal,
        now: datetime,
        lease_expires_at: datetime,
        running_limit: int = 1,
    ) -> ReversionJob | None:
        """Append an attempt and allocate its per-principal sequence atomically.

        Database restore can rewind this high-water mark relative to retained broker state;
        later runtime integration must reconcile that gap and must not infer completeness here.
        """

        if type(running_limit) is not int or running_limit <= 0:
            raise ValueError("Reverse running limit must be a positive integer")
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                lock_reverse_claim(database, self._engine.dialect.name)
                principal_row = self._lock_principal(database, principal.principal_id)
                if (
                    not principal_row.reconciliation_complete
                    or principal_row.reconciliation_token is not None
                ):
                    return None
                running = database.scalar(
                    select(func.count())
                    .select_from(ReversionJobRow)
                    .where(ReversionJobRow.state == ReversionJobState.RUNNING.value)
                )
                if int(running or 0) >= running_limit:
                    return None
                unbound_attempt = database.scalar(
                    select(ReversionAttemptRow.attempt_id)
                    .join(
                        ReversionJobRow,
                        ReversionJobRow.current_attempt_id
                        == ReversionAttemptRow.attempt_id,
                    )
                    .where(
                        ReversionAttemptRow.principal_id == str(principal.principal_id),
                        ReversionAttemptRow.unit_id.is_(None),
                        ReversionJobRow.state == ReversionJobState.RUNNING.value,
                    )
                    .limit(1)
                )
                if unbound_attempt is not None:
                    return None
                statement = (
                    select(ReversionJobRow)
                    .where(
                        ReversionJobRow.state == ReversionJobState.QUEUED.value,
                        ReversionJobRow.source_ready.is_(True),
                    )
                    .order_by(ReversionJobRow.created_at, ReversionJobRow.id)
                    .limit(1)
                )
                if self._engine.dialect.name == "postgresql":
                    statement = statement.with_for_update(skip_locked=True)
                row = database.scalar(statement)
                if row is None:
                    return None
                sequence = self._next_create_sequence(database, principal_row)
                attempt_number = row.attempt + 1
                attempt_id = uuid4()
                lease_token = uuid4()
                attempt = ReversionAttemptRow(
                    attempt_id=str(attempt_id),
                    job_id=row.id,
                    attempt_number=attempt_number,
                    worker_id=worker_id,
                    lease_token=str(lease_token),
                    leased_at=now,
                    heartbeat_at=now,
                    lease_expires_at=lease_expires_at,
                    principal_id=str(principal.principal_id),
                    create_sequence=sequence,
                )
                database.add(attempt)
                row.state = ReversionJobState.RUNNING.value
                row.step = ReversionJobStep.ISOLATING.value
                row.updated_at = now
                row.attempt = attempt_number
                row.lease_owner = worker_id
                row.lease_token = str(lease_token)
                row.lease_expires_at = lease_expires_at
                row.heartbeat_at = now
                row.current_attempt_id = str(attempt_id)
                row.cancel_requested = False
                database.flush()
                return _job(row)
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def heartbeat(self, heartbeat: ReversionLeaseHeartbeat) -> bool:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                job_statement = (
                    update(ReversionJobRow)
                    .where(
                        *self._owned_current(
                            heartbeat.job_id,
                            heartbeat.attempt_id,
                            heartbeat.worker_id,
                            heartbeat.lease_token,
                        ),
                        ReversionJobRow.lease_expires_at >= heartbeat.now,
                        ReversionJobRow.heartbeat_at <= heartbeat.now,
                    )
                    .values(
                        heartbeat_at=heartbeat.now,
                        lease_expires_at=heartbeat.lease_expires_at,
                        updated_at=heartbeat.now,
                        step=heartbeat.step.value,
                    )
                )
                result = database.execute(job_statement)
                if getattr(result, "rowcount", 0) != 1:
                    return False
                attempt_result = database.execute(
                    update(ReversionAttemptRow)
                    .where(
                        ReversionAttemptRow.attempt_id == str(heartbeat.attempt_id),
                        ReversionAttemptRow.job_id == str(heartbeat.job_id),
                        ReversionAttemptRow.worker_id == heartbeat.worker_id,
                        ReversionAttemptRow.lease_token == str(heartbeat.lease_token),
                        ReversionAttemptRow.heartbeat_at <= heartbeat.now,
                    )
                    .values(
                        heartbeat_at=heartbeat.now,
                        lease_expires_at=heartbeat.lease_expires_at,
                    )
                )
                if getattr(attempt_result, "rowcount", 0) != 1:
                    raise ReversionJobRepositoryError
                return True
        except ReversionJobRepositoryError:
            raise
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def cancellation_requested(
        self, job_id: UUID, attempt_id: UUID, worker_id: str, lease_token: UUID
    ) -> bool:
        try:
            with DatabaseSession(self._engine) as database:
                value = database.scalar(
                    select(ReversionJobRow.cancel_requested).where(
                        *self._owned_current(job_id, attempt_id, worker_id, lease_token)
                    )
                )
                return bool(value)
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def request_cancel(
        self, job_id: UUID, owner_id: UUID, now: datetime, expires_at: datetime
    ) -> ReversionJob | None:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._update_job(
                    database,
                    update(ReversionJobRow)
                    .where(
                        ReversionJobRow.id == str(job_id),
                        ReversionJobRow.owner_id == str(owner_id),
                        ReversionJobRow.state.in_(ACTIVE_JOB_STATES),
                    )
                    .values(
                        state=case(
                            (
                                ReversionJobRow.state == ReversionJobState.QUEUED.value,
                                ReversionJobState.CANCELLED.value,
                            ),
                            else_=ReversionJobRow.state,
                        ),
                        updated_at=now,
                        expires_at=case(
                            (
                                ReversionJobRow.state == ReversionJobState.QUEUED.value,
                                expires_at,
                            ),
                            else_=ReversionJobRow.expires_at,
                        ),
                        cancel_requested=case(
                            (
                                ReversionJobRow.state
                                == ReversionJobState.RUNNING.value,
                                True,
                            ),
                            else_=False,
                        ),
                    ),
                    str(job_id),
                )
                if row is not None:
                    return _job(row)
                existing = database.scalar(
                    select(ReversionJobRow).where(
                        ReversionJobRow.id == str(job_id),
                        ReversionJobRow.owner_id == str(owner_id),
                    )
                )
                return _job(existing) if existing is not None else None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def reserve_create_intent(  # noqa: PLR0913, PLR0917 - explicit fence
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        policy_revision: str,
        policy_specification: EvidenceDigest,
        now: datetime,
    ) -> ReversionAttempt:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                self._require_active_lease(
                    database, job_id, attempt_id, worker_id, lease_token, now
                )
                self._before_active_attempt_lock()
                row = database.get(
                    ReversionAttemptRow,
                    str(attempt_id),
                    with_for_update=self._engine.dialect.name == "postgresql",
                )
                if row is None:
                    raise ReversionJobLeaseLostError
                if row.create_intent_at is not None:
                    if (
                        row.policy_revision != policy_revision
                        or row.policy_specification != policy_specification.value
                    ):
                        raise ReversionJobConflictError(
                            "Reverse broker create intent conflicts"
                        )
                    return _attempt(row)
                row.create_intent_at = now
                row.policy_revision = policy_revision
                row.policy_specification = policy_specification.value
                database.flush()
                return _attempt(row)
        except ReversionJobLeaseLostError, ReversionJobConflictError:
            raise
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def record_broker_unit(  # noqa: PLR0913, PLR0917 - explicit fence
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        unit_id: UUID,
        now: datetime,
    ) -> ReversionAttempt:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                self._require_active_lease(
                    database, job_id, attempt_id, worker_id, lease_token, now
                )
                self._before_active_attempt_lock()
                row = database.get(
                    ReversionAttemptRow,
                    str(attempt_id),
                    with_for_update=self._engine.dialect.name == "postgresql",
                )
                if row is None or row.create_intent_at is None:
                    raise ReversionJobConflictError("Reverse create intent is missing")
                if row.unit_id is not None and row.unit_id != str(unit_id):
                    raise ReversionJobConflictError("Reverse broker unit conflicts")
                row.unit_id = str(unit_id)
                database.flush()
                return _attempt(row)
        except ReversionJobLeaseLostError, ReversionJobConflictError:
            raise
        except IntegrityError:
            raise ReversionJobConflictError("Reverse broker unit conflicts") from None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def record_active_termination_proof(  # noqa: PLR0913, PLR0917 - explicit fence
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        proof: TerminationProof,
        now: datetime,
    ) -> ReversionAttempt:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                self._require_active_lease(
                    database, job_id, attempt_id, worker_id, lease_token, now
                )
                self._before_active_attempt_lock()
                row = database.get(
                    ReversionAttemptRow,
                    str(attempt_id),
                    with_for_update=self._engine.dialect.name == "postgresql",
                )
                if row is None:
                    raise ReversionJobLeaseLostError
                return self._store_proof(database, row, proof, now)
        except ReversionJobLeaseLostError, ReversionJobConflictError:
            raise
        except IntegrityError:
            raise ReversionJobConflictError(
                "Reverse termination proof conflicts"
            ) from None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def claim_recovery(
        self,
        recovery_owner: str,
        now: datetime,
        recovery_expires_at: datetime,
        limit: int,
    ) -> tuple[ReversionAttempt, ...]:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                statement = (
                    select(ReversionAttemptRow)
                    .join(
                        ReversionJobRow,
                        and_(
                            ReversionJobRow.id == ReversionAttemptRow.job_id,
                            ReversionJobRow.current_attempt_id
                            == ReversionAttemptRow.attempt_id,
                        ),
                    )
                    .where(
                        ReversionJobRow.state == ReversionJobState.RUNNING.value,
                        ReversionJobRow.lease_expires_at < now,
                        ReversionAttemptRow.create_intent_at.is_not(None),
                        ReversionAttemptRow.proof_recorded_at.is_(None),
                        or_(
                            ReversionAttemptRow.recovery_token.is_(None),
                            ReversionAttemptRow.recovery_expires_at < now,
                        ),
                    )
                    .order_by(
                        ReversionJobRow.lease_expires_at,
                        ReversionAttemptRow.attempt_id,
                    )
                    .limit(limit)
                )
                if self._engine.dialect.name == "postgresql":
                    statement = statement.with_for_update(skip_locked=True)
                rows = tuple(database.scalars(statement))
                claimed: list[ReversionAttempt] = []
                for row in rows:
                    row.recovery_owner = recovery_owner
                    row.recovery_token = str(uuid4())
                    row.recovery_expires_at = recovery_expires_at
                    database.flush()
                    claimed.append(_attempt(row))
                return tuple(claimed)
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def record_recovery_termination_proof(
        self,
        job_id: UUID,
        attempt_id: UUID,
        recovery_token: UUID,
        proof: TerminationProof,
        now: datetime,
    ) -> ReversionAttempt:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                statement = (
                    select(ReversionAttemptRow)
                    .join(
                        ReversionJobRow,
                        and_(
                            ReversionJobRow.id == ReversionAttemptRow.job_id,
                            ReversionJobRow.current_attempt_id
                            == ReversionAttemptRow.attempt_id,
                        ),
                    )
                    .where(
                        ReversionJobRow.id == str(job_id),
                        ReversionJobRow.state == ReversionJobState.RUNNING.value,
                        ReversionAttemptRow.attempt_id == str(attempt_id),
                        ReversionAttemptRow.recovery_token == str(recovery_token),
                        ReversionAttemptRow.recovery_expires_at >= now,
                        ReversionAttemptRow.proof_id.is_(None),
                        ReversionAttemptRow.proof_recorded_at.is_(None),
                    )
                )
                if self._engine.dialect.name == "postgresql":
                    statement = statement.with_for_update()
                self._before_recovery_proof_cas()
                row = database.scalar(statement)
                if row is None:
                    persisted = database.scalar(
                        select(ReversionAttemptRow)
                        .join(
                            ReversionJobRow,
                            ReversionJobRow.id == ReversionAttemptRow.job_id,
                        )
                        .where(
                            ReversionJobRow.id == str(job_id),
                            ReversionAttemptRow.attempt_id == str(attempt_id),
                        )
                    )
                    if persisted is None or persisted.proof_id is None:
                        raise ReversionJobLeaseLostError(
                            "Reverse recovery lease was lost"
                        )
                    if (
                        persisted.proof_recovery_token != str(recovery_token)
                        or _attempt(persisted).termination_proof != proof
                    ):
                        raise ReversionJobConflictError(
                            "Reverse recovery proof replay conflicts"
                        )
                    return _attempt(persisted)
                self._store_proof(
                    database, row, proof, now, recovery_token=recovery_token
                )
                row.recovery_owner = None
                row.recovery_token = None
                row.recovery_expires_at = None
                database.flush()
                return _attempt(row)
        except ReversionJobLeaseLostError, ReversionJobConflictError:
            raise
        except IntegrityError:
            raise ReversionJobConflictError(
                "Reverse termination proof conflicts"
            ) from None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def _before_recovery_proof_cas(self) -> None:
        """Private synchronization seam overridden only by concurrency tests."""

    def _store_proof(
        self,
        database: DatabaseSession,
        row: ReversionAttemptRow,
        proof: TerminationProof,
        now: datetime,
        *,
        recovery_token: UUID | None = None,
    ) -> ReversionAttempt:
        expected_unit = row.unit_id
        if (
            row.create_intent_at is None
            or row.policy_revision is None
            or proof.attempt_id != UUID(row.attempt_id)
            or proof.principal.principal_id != UUID(row.principal_id)
            or proof.policy_revision != row.policy_revision
            or (expected_unit is not None and proof.unit_id != UUID(expected_unit))
        ):
            raise ReversionJobConflictError("Reverse termination proof does not match")
        if row.proof_id is not None:
            existing = _attempt(row).termination_proof
            if existing != proof:
                raise ReversionJobConflictError("Reverse termination proof conflicts")
            return _attempt(row)
        row.unit_id = str(proof.unit_id)
        row.proof_id = str(proof.proof_id)
        row.proof_unit_id = str(proof.unit_id)
        row.proof_principal_id = str(proof.principal.principal_id)
        row.proof_policy_revision = proof.policy_revision
        row.exit_evidence = proof.exit_evidence.value
        row.empty_evidence = proof.empty_evidence.value
        row.removal_evidence = proof.removal_evidence.value
        row.proof_recorded_at = now
        row.proof_recovery_token = (
            str(recovery_token) if recovery_token is not None else None
        )
        database.flush()
        return _attempt(row)

    def acknowledge_termination_proof(
        self, attempt_id: UUID, proof_id: UUID, now: datetime
    ) -> ReversionAttempt:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = database.scalar(
                    select(ReversionAttemptRow).where(
                        ReversionAttemptRow.attempt_id == str(attempt_id),
                        ReversionAttemptRow.proof_id == str(proof_id),
                        ReversionAttemptRow.proof_recorded_at.is_not(None),
                    )
                )
                if row is None:
                    raise ReversionJobConflictError(
                        "Reverse termination proof is not durable"
                    )
                if row.proof_acknowledged_at is None:
                    row.proof_acknowledged_at = now
                    database.flush()
                return _attempt(row)
        except ReversionJobConflictError:
            raise
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def recover_expired_leases(
        self,
        now: datetime,
        expires_at: datetime,
        incomplete_before: datetime,
        limit: int | None = None,
    ) -> ReversionLeaseRecoveryResult:
        if limit is not None and (type(limit) is not int or limit <= 0):
            raise ValueError("Reverse recovery limit must be a positive integer")
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                statement = (
                    select(ReversionJobRow, ReversionAttemptRow)
                    .join(
                        ReversionAttemptRow,
                        ReversionAttemptRow.attempt_id
                        == ReversionJobRow.current_attempt_id,
                    )
                    .where(
                        ReversionJobRow.state == ReversionJobState.RUNNING.value,
                        ReversionJobRow.lease_expires_at < now,
                        or_(
                            ReversionAttemptRow.create_intent_at.is_(None),
                            ReversionAttemptRow.proof_recorded_at.is_not(None),
                        ),
                    )
                    .order_by(ReversionJobRow.id)
                )
                if limit is not None:
                    statement = statement.limit(limit)
                if self._engine.dialect.name == "postgresql":
                    statement = statement.with_for_update(skip_locked=True)
                requeued = 0
                cancelled_count = 0
                for job, attempt in database.execute(statement):
                    cancelled = job.cancel_requested
                    job.state = (
                        ReversionJobState.CANCELLED.value
                        if cancelled
                        else ReversionJobState.QUEUED.value
                    )
                    job.step = job.step if cancelled else ReversionJobStep.QUEUED.value
                    job.updated_at = now
                    job.expires_at = expires_at if cancelled else job.expires_at
                    for key, value in self._clear_lease().items():
                        setattr(job, key, value)
                    attempt.recovery_owner = None
                    attempt.recovery_token = None
                    attempt.recovery_expires_at = None
                    if cancelled:
                        cancelled_count += 1
                    else:
                        requeued += 1
                recovered = ReversionLeaseRecoveryResult(requeued, cancelled_count, 0)
                remaining = None if limit is None else limit - recovered.progressed
                if remaining == 0:
                    return recovered
                incomplete_ids = (
                    select(ReversionJobRow.id)
                    .where(
                        ReversionJobRow.state == ReversionJobState.QUEUED.value,
                        ReversionJobRow.source_ready.is_(False),
                        ReversionJobRow.created_at <= incomplete_before,
                    )
                    .order_by(ReversionJobRow.id)
                )
                if remaining is not None:
                    incomplete_ids = incomplete_ids.limit(remaining)
                if self._engine.dialect.name == "postgresql":
                    incomplete_ids = incomplete_ids.with_for_update(skip_locked=True)
                candidates = tuple(database.scalars(incomplete_ids))
                if not candidates:
                    return recovered
                self._after_incomplete_recovery_select()
                incomplete_result = database.execute(
                    update(ReversionJobRow)
                    .where(
                        ReversionJobRow.id.in_(candidates),
                        ReversionJobRow.state == ReversionJobState.QUEUED.value,
                        ReversionJobRow.source_ready.is_(False),
                        ReversionJobRow.created_at <= incomplete_before,
                    )
                    .values(
                        state=ReversionJobState.FAILED.value,
                        error_code="source_upload_incomplete",
                        error_message="Reverse source upload did not complete.",
                        updated_at=now,
                        expires_at=expires_at,
                    )
                )
                return ReversionLeaseRecoveryResult(
                    recovered.requeued,
                    recovered.cancelled,
                    int(getattr(incomplete_result, "rowcount", 0)),
                )
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def _after_incomplete_recovery_select(self) -> None:
        """Private synchronization seam overridden only by concurrency tests."""

    def succeed(  # noqa: PLR0913, PLR0917
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        result_object_id: UUID,
        result_mode: ReverseOutputMode,
        result_sha256: str,
        result_size: int,
        trace: ReversionTraceMetadata,
        now: datetime,
        expires_at: datetime,
    ) -> ReversionJob:
        if (
            type(result_object_id) is not UUID
            or type(result_mode) is not ReverseOutputMode
            or len(result_sha256) != SHA256_CHARACTERS
            or any(character not in "0123456789abcdef" for character in result_sha256)
            or type(result_size) is not int
            or result_size <= 0
            or type(trace) is not ReversionTraceMetadata
            or trace.result_mode is not result_mode
        ):
            raise ReversionJobConflictError("Reverse result metadata is invalid")
        return self._finish(
            job_id,
            attempt_id,
            worker_id,
            lease_token,
            now,
            expires_at,
            {
                "state": ReversionJobState.SUCCEEDED.value,
                "step": ReversionJobStep.COMPLETE.value,
                "result_object_id": str(result_object_id),
                "result_mode": result_mode.value,
                "result_sha256": result_sha256,
                "result_size": result_size,
                "trace_metadata": _trace_json(trace),
            },
            require_proof=True,
            cancellation_wins=True,
            trace=trace,
        )

    def fail(self, failure: ReversionFailure) -> ReversionJob:
        return self._finish(
            failure.job_id,
            failure.attempt_id,
            failure.worker_id,
            failure.lease_token,
            failure.now,
            failure.expires_at,
            {
                "state": ReversionJobState.FAILED.value,
                "error_code": failure.code,
                "error_message": failure.message,
            },
            require_proof=False,
            cancellation_wins=True,
        )

    def finish_cancelled(  # noqa: PLR0913, PLR0917 - explicit fence
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> ReversionJob:
        return self._finish(
            job_id,
            attempt_id,
            worker_id,
            lease_token,
            now,
            expires_at,
            {"state": ReversionJobState.CANCELLED.value},
            require_proof=False,
            cancellation_wins=False,
        )

    def _finish(  # noqa: PLR0913, PLR0917
        self,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        now: datetime,
        expires_at: datetime,
        values: dict[str, object],
        *,
        require_proof: bool,
        cancellation_wins: bool,
        trace: ReversionTraceMetadata | None = None,
    ) -> ReversionJob:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                job = self._require_active_lease(
                    database, job_id, attempt_id, worker_id, lease_token, now
                )
                attempt = database.get(ReversionAttemptRow, str(attempt_id))
                if attempt is None:
                    raise ReversionJobLeaseLostError
                if (require_proof or attempt.create_intent_at is not None) and (
                    attempt.proof_recorded_at is None
                ):
                    raise ReversionProofRequiredError(
                        "Reverse termination proof is required"
                    )
                if require_proof and values.get("result_object_id") != str(
                    reversion_result_object_id(job_id, attempt.attempt_number)
                ):
                    raise ReversionJobConflictError(
                        "Reverse result object identity is not attempt-scoped"
                    )
                if trace is not None and (
                    trace.source_family.value != job.source_family
                    or trace.detected_format != job.parser_format
                ):
                    raise ReversionJobConflictError(
                        "Reverse result trace does not match source admission"
                    )
                if cancellation_wins and job.cancel_requested:
                    values = {"state": ReversionJobState.CANCELLED.value}
                values.update(
                    {
                        "updated_at": now,
                        "expires_at": expires_at,
                        **self._clear_lease(),
                    }
                )
                row = self._update_job(
                    database,
                    update(ReversionJobRow)
                    .where(
                        *self._owned_current(
                            job_id, attempt_id, worker_id, lease_token
                        ),
                        ReversionJobRow.lease_expires_at >= now,
                    )
                    .values(**values),
                    str(job_id),
                )
                if row is None:
                    raise ReversionJobLeaseLostError
                return _job(row)
        except (
            ReversionJobLeaseLostError,
            ReversionProofRequiredError,
            ReversionJobConflictError,
        ):
            raise
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def _require_active_lease(  # noqa: PLR0913, PLR0917 - explicit fence
        self,
        database: DatabaseSession,
        job_id: UUID,
        attempt_id: UUID,
        worker_id: str,
        lease_token: UUID,
        now: datetime,
    ) -> ReversionJobRow:
        statement = select(ReversionJobRow).where(
            *self._owned_current(job_id, attempt_id, worker_id, lease_token),
            ReversionJobRow.lease_expires_at >= now,
        )
        if self._engine.dialect.name == "postgresql":
            statement = statement.with_for_update()
        row = database.scalar(statement)
        if row is None:
            raise ReversionJobLeaseLostError("Reverse job lease was lost")
        return row
