"""Atomic SQLite/PostgreSQL durable reverse-job repository."""

from __future__ import annotations

import json
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from markweave.broker.models import (
    MAX_SEQUENCE,
    AuthenticatedPrincipal,
    EvidenceDigest,
    TerminationProof,
)
from markweave.broker.reconciliation_protocol import (
    ReconciliationResponse,
    ReconciliationTombstone,
)
from markweave.persistence.job_admission import (
    ACTIVE_JOB_STATES,
    global_active_job_count,
    lock_global_admission,
    lock_reverse_claim,
)
from markweave.persistence.reversion_jobs.common import (
    _attempt,
    _job,
    _required_utc,
    _SqlReversionStore,
    _trace_json,
)
from markweave.persistence.schema import (
    ReversionAttemptRow,
    ReversionBrokerPrincipalRow,
    ReversionJobRow,
    ReversionOrphanProofRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.reversion_jobs.errors import (
    ReversionJobConflictError,
    ReversionJobLeaseLostError,
    ReversionJobRepositoryError,
    ReversionJobUserQuotaExceededError,
    ReversionProofRequiredError,
    ReversionQueueCapacityExceededError,
)
from markweave.reversion_jobs.models import (
    SHA256_CHARACTERS,
    TERMINAL_REVERSION_STATES,
    ExpiredReversionObjects,
    ReversionAttempt,
    ReversionFailure,
    ReversionJob,
    ReversionJobPage,
    ReversionJobState,
    ReversionJobStep,
    ReversionLeaseHeartbeat,
    ReversionSubmission,
    ReversionTraceMetadata,
    reversion_result_object_id,
)
from markweave.reversions.models import ReverseOutputMode

_MAX_WORKER_ID_LENGTH = 255


class SqlReversionJobRepository(_SqlReversionStore):
    """Complete reverse queue contract with owner-bound public reads."""

    def create(self, submission: ReversionSubmission) -> tuple[ReversionJob, bool]:
        row = ReversionJobRow(
            id=str(submission.id),
            owner_id=str(submission.owner_id),
            source_object_id=str(submission.source_object_id),
            source_stem=submission.source_stem,
            source_extension=submission.admission.extension,
            source_family=submission.admission.family.value,
            detected_format=submission.admission.detected_format,
            parser_format=submission.admission.parser_format,
            source_sha256=submission.source_sha256,
            source_size=submission.source_size,
            component_versions=json.dumps(
                submission.component_versions, separators=(",", ":")
            ),
            request_digest=submission.request_digest,
            idempotency_digest=submission.idempotency_digest,
            correlation_id=submission.correlation_id,
            state=ReversionJobState.QUEUED.value,
            step=ReversionJobStep.QUEUED.value,
            created_at=submission.created_at,
            updated_at=submission.created_at,
            attempt=0,
            source_ready=False,
            cancel_requested=False,
            cleanup_completed=False,
        )
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                if self._admission_policy is not None:
                    lock_global_admission(database, self._engine.dialect.name)
                replay = self._find_idempotent(database, submission)
                if replay is not None:
                    return replay, True
                self._after_idempotency_miss()
                self._enforce_admission(database, submission.owner_id)
                database.add(row)
                database.flush()
                return _job(row), False
        except ReversionJobUserQuotaExceededError, ReversionQueueCapacityExceededError:
            raise
        except IntegrityError:
            if submission.idempotency_digest is None:
                raise ReversionJobRepositoryError from None
            self._after_idempotency_collision()
            replay = self._get_idempotent(
                submission.owner_id, submission.idempotency_digest
            )
            if replay is None:
                raise ReversionJobRepositoryError from None
            if replay.request_digest != submission.request_digest:
                raise ReversionJobConflictError(
                    "Reverse idempotency key conflicts"
                ) from None
            return replay, True
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def _after_idempotency_miss(self) -> None:
        """Private synchronization seam overridden only by concurrency tests."""

    def _after_idempotency_collision(self) -> None:
        """Private observation seam overridden only by concurrency tests."""

    @staticmethod
    def _find_idempotent(
        database: DatabaseSession, submission: ReversionSubmission
    ) -> ReversionJob | None:
        if submission.idempotency_digest is None:
            return None
        row = database.scalar(
            select(ReversionJobRow).where(
                ReversionJobRow.owner_id == str(submission.owner_id),
                ReversionJobRow.idempotency_digest == submission.idempotency_digest,
            )
        )
        if row is None:
            return None
        if row.request_digest != submission.request_digest:
            raise ReversionJobConflictError("Reverse idempotency key conflicts")
        return _job(row)

    def _get_idempotent(self, owner_id: UUID, digest: str) -> ReversionJob | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.scalar(
                    select(ReversionJobRow).where(
                        ReversionJobRow.owner_id == str(owner_id),
                        ReversionJobRow.idempotency_digest == digest,
                    )
                )
                return _job(row) if row is not None else None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def _enforce_admission(self, database: DatabaseSession, owner_id: UUID) -> None:
        policy = self._admission_policy
        if policy is None:
            return
        owner_active = database.scalar(
            select(func.count())
            .select_from(ReversionJobRow)
            .where(
                ReversionJobRow.owner_id == str(owner_id),
                ReversionJobRow.state.in_(ACTIVE_JOB_STATES),
            )
        )
        if int(owner_active or 0) >= policy.active_jobs_per_user:
            raise ReversionJobUserQuotaExceededError(
                "Active reverse-job quota exceeded"
            )
        if global_active_job_count(database) >= policy.global_queue_capacity:
            raise ReversionQueueCapacityExceededError(
                "Shared conversion queue capacity exceeded"
            )

    def activate_source(self, job_id: UUID, now: datetime) -> ReversionJob:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._update_job(
                    database,
                    update(ReversionJobRow)
                    .where(
                        ReversionJobRow.id == str(job_id),
                        ReversionJobRow.state == ReversionJobState.QUEUED.value,
                        ReversionJobRow.source_ready.is_(False),
                    )
                    .values(source_ready=True, updated_at=now),
                    str(job_id),
                )
                if row is not None:
                    return _job(row)
                existing = database.get(ReversionJobRow, str(job_id))
                if (
                    existing is None
                    or existing.state != ReversionJobState.QUEUED.value
                    or not existing.source_ready
                ):
                    raise ReversionJobRepositoryError
                return _job(existing)
        except ReversionJobRepositoryError:
            raise
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def get_owner(self, job_id: UUID, owner_id: UUID) -> ReversionJob | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.scalar(
                    select(ReversionJobRow).where(
                        ReversionJobRow.id == str(job_id),
                        ReversionJobRow.owner_id == str(owner_id),
                    )
                )
                return _job(row) if row is not None else None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def list_owner(
        self, owner_id: UUID, *, offset: int, limit: int
    ) -> ReversionJobPage:
        try:
            with DatabaseSession(self._engine) as database:
                owner = str(owner_id)
                total = database.scalar(
                    select(func.count())
                    .select_from(ReversionJobRow)
                    .where(ReversionJobRow.owner_id == owner)
                )
                rows = database.scalars(
                    select(ReversionJobRow)
                    .where(ReversionJobRow.owner_id == owner)
                    .order_by(
                        ReversionJobRow.created_at.desc(), ReversionJobRow.id.desc()
                    )
                    .offset(offset)
                    .limit(limit)
                )
                return ReversionJobPage(
                    tuple(_job(row) for row in rows), int(total or 0), offset, limit
                )
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def get_internal(self, job_id: UUID) -> ReversionJob | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.get(ReversionJobRow, str(job_id))
                return _job(row) if row is not None else None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def get_attempt(self, attempt_id: UUID) -> ReversionAttempt | None:
        try:
            with DatabaseSession(self._engine) as database:
                row = database.get(ReversionAttemptRow, str(attempt_id))
                return _attempt(row) if row is not None else None
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def list_attempts(self, job_id: UUID) -> tuple[ReversionAttempt, ...]:
        try:
            with DatabaseSession(self._engine) as database:
                rows = database.scalars(
                    select(ReversionAttemptRow)
                    .where(ReversionAttemptRow.job_id == str(job_id))
                    .order_by(ReversionAttemptRow.attempt_number)
                )
                return tuple(_attempt(row) for row in rows)
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

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

    def _lock_principal(
        self, database: DatabaseSession, principal_id: UUID
    ) -> ReversionBrokerPrincipalRow:
        principal = str(principal_id)
        database.execute(
            text(
                "INSERT INTO reversion_broker_principals "
                "(principal_id, create_sequence_high_water) VALUES (:principal, 0) "
                "ON CONFLICT (principal_id) DO NOTHING"
            ),
            {"principal": principal},
        )
        statement = select(ReversionBrokerPrincipalRow).where(
            ReversionBrokerPrincipalRow.principal_id == principal
        )
        if self._engine.dialect.name == "postgresql":
            statement = statement.with_for_update()
        row = database.scalar(statement)
        if row is None:
            raise ReversionJobRepositoryError
        return row

    @staticmethod
    def _next_create_sequence(
        database: DatabaseSession, row: ReversionBrokerPrincipalRow
    ) -> int:
        if row.create_sequence_high_water >= MAX_SEQUENCE:
            raise ReversionJobRepositoryError
        row.create_sequence_high_water += 1
        database.flush()
        return row.create_sequence_high_water

    def begin_reconciliation(
        self,
        principal: AuthenticatedPrincipal,
        owner: str,
        token: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> int:
        """Acquire the principal-exclusive durable reconciliation lease."""

        if not owner or len(owner) > _MAX_WORKER_ID_LENGTH or expires_at <= now:
            raise ValueError("Reconciliation lease is invalid")
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                if self._engine.dialect.name == "postgresql":
                    database.execute(text("SELECT pg_advisory_xact_lock(1830285107)"))
                row = self._lock_principal(database, principal.principal_id)
                exact_replay = row.reconciliation_token == str(token)
                if (
                    row.reconciliation_token is not None
                    and row.reconciliation_token != str(token)
                    and row.reconciliation_expires_at is not None
                    and _required_utc(row.reconciliation_expires_at) >= now
                ):
                    raise ReversionJobLeaseLostError
                if not exact_replay:
                    row.reconciliation_cursor = 0
                    row.reconciliation_observed_head = None
                    row.reconciliation_fixed_point = False
                row.reconciliation_complete = False
                row.reconciliation_owner = owner
                row.reconciliation_token = str(token)
                row.reconciliation_expires_at = expires_at
                database.flush()
                return row.reconciliation_cursor
        except ReversionJobLeaseLostError:
            raise
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def record_reconciliation_page(
        self,
        principal: AuthenticatedPrincipal,
        token: UUID,
        page: ReconciliationResponse,
        now: datetime,
    ) -> ReconciliationTombstone | None:
        """Atomically advance HWM and retain a proof plus ACK intent."""

        if page.principal_id != principal.principal_id:
            raise ReversionJobConflictError
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                if self._engine.dialect.name == "postgresql":
                    database.execute(text("SELECT pg_advisory_xact_lock(1830285107)"))
                row = self._lock_principal(database, principal.principal_id)
                if (
                    row.reconciliation_token != str(token)
                    or row.reconciliation_expires_at is None
                    or _required_utc(row.reconciliation_expires_at) < now
                    or page.after_create_sequence != row.reconciliation_cursor
                ):
                    raise ReversionJobLeaseLostError
                row.create_sequence_high_water = max(
                    row.create_sequence_high_water,
                    page.create_sequence_high_water,
                )
                tombstone = page.tombstone
                if tombstone is not None:
                    self._retain_reconciliation_tombstone(database, row, tombstone, now)
                    row.reconciliation_cursor = tombstone.create_sequence
                    row.reconciliation_observed_head = None
                    row.reconciliation_fixed_point = False
                elif (
                    row.reconciliation_observed_head == page.create_sequence_high_water
                ):
                    row.reconciliation_fixed_point = True
                else:
                    row.reconciliation_observed_head = page.create_sequence_high_water
                    row.reconciliation_fixed_point = False
                database.flush()
                return tombstone
        except ReversionJobConflictError, ReversionJobLeaseLostError:
            raise
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def _retain_reconciliation_tombstone(  # noqa: PLR0912 - explicit conflict fence
        self,
        database: DatabaseSession,
        principal_row: ReversionBrokerPrincipalRow,
        tombstone: ReconciliationTombstone,
        now: datetime,
    ) -> None:
        proof = tombstone.proof
        attempt_statement = select(ReversionAttemptRow).where(
            ReversionAttemptRow.principal_id == principal_row.principal_id,
            ReversionAttemptRow.create_sequence == tombstone.create_sequence,
        )
        if self._engine.dialect.name == "postgresql":
            attempt_statement = attempt_statement.with_for_update()
        attempt = database.scalar(attempt_statement)
        if attempt is not None:
            self._after_reconciliation_attempt_lock()
        attempt_collision = database.scalar(
            select(ReversionAttemptRow.attempt_id)
            .where(
                or_(
                    ReversionAttemptRow.attempt_id == str(proof.attempt_id),
                    ReversionAttemptRow.unit_id == str(proof.unit_id),
                    ReversionAttemptRow.proof_id == str(proof.proof_id),
                )
            )
            .limit(1)
        )
        orphan_collision = database.scalar(
            select(ReversionOrphanProofRow)
            .where(
                or_(
                    ReversionOrphanProofRow.attempt_id == str(proof.attempt_id),
                    ReversionOrphanProofRow.unit_id == str(proof.unit_id),
                    ReversionOrphanProofRow.proof_id == str(proof.proof_id),
                )
            )
            .limit(1)
        )
        if attempt_collision is not None and (
            attempt is None or attempt_collision != attempt.attempt_id
        ):
            raise ReversionJobConflictError
        if attempt is None:
            existing = database.get(
                ReversionOrphanProofRow,
                (principal_row.principal_id, tombstone.create_sequence),
            )
            values = (
                str(proof.attempt_id),
                str(proof.unit_id),
                str(proof.proof_id),
                proof.policy_revision,
                tombstone.policy_specification.value,
                proof.exit_evidence.value,
                proof.empty_evidence.value,
                proof.removal_evidence.value,
            )
            if existing is not None:
                persisted = (
                    existing.attempt_id,
                    existing.unit_id,
                    existing.proof_id,
                    existing.policy_revision,
                    existing.policy_specification,
                    existing.exit_evidence,
                    existing.empty_evidence,
                    existing.removal_evidence,
                )
                if persisted != values:
                    raise ReversionJobConflictError
                return
            if orphan_collision is not None:
                raise ReversionJobConflictError
            database.add(
                ReversionOrphanProofRow(
                    principal_id=principal_row.principal_id,
                    create_sequence=tombstone.create_sequence,
                    attempt_id=values[0],
                    unit_id=values[1],
                    proof_id=values[2],
                    policy_revision=values[3],
                    policy_specification=values[4],
                    exit_evidence=values[5],
                    empty_evidence=values[6],
                    removal_evidence=values[7],
                    recorded_at=now,
                    ack_intent_at=now,
                )
            )
            return
        if orphan_collision is not None:
            raise ReversionJobConflictError
        if attempt.attempt_id != str(proof.attempt_id) or attempt.principal_id != str(
            proof.principal.principal_id
        ):
            raise ReversionJobConflictError
        if attempt.create_intent_at is None:
            if any(
                value is not None
                for value in (
                    attempt.policy_revision,
                    attempt.policy_specification,
                    attempt.unit_id,
                )
            ):
                raise ReversionJobConflictError
            attempt.create_intent_at = now
            attempt.policy_revision = proof.policy_revision
            attempt.policy_specification = tombstone.policy_specification.value
        elif (
            attempt.policy_revision != proof.policy_revision
            or attempt.policy_specification != tombstone.policy_specification.value
            or attempt.unit_id not in {None, str(proof.unit_id)}
        ):
            raise ReversionJobConflictError
        existing_proof = (
            attempt.proof_id,
            attempt.proof_unit_id,
            attempt.proof_principal_id,
            attempt.proof_policy_revision,
            attempt.exit_evidence,
            attempt.empty_evidence,
            attempt.removal_evidence,
        )
        values = (
            str(proof.proof_id),
            str(proof.unit_id),
            str(proof.principal.principal_id),
            proof.policy_revision,
            proof.exit_evidence.value,
            proof.empty_evidence.value,
            proof.removal_evidence.value,
        )
        if attempt.proof_id is not None and existing_proof != values:
            raise ReversionJobConflictError
        attempt.unit_id = str(proof.unit_id)
        if attempt.proof_id is None:
            (
                attempt.proof_id,
                attempt.proof_unit_id,
                attempt.proof_principal_id,
                attempt.proof_policy_revision,
                attempt.exit_evidence,
                attempt.empty_evidence,
                attempt.removal_evidence,
            ) = values
            attempt.proof_recorded_at = now
        attempt.reconciliation_ack_intent_at = (
            attempt.reconciliation_ack_intent_at or now
        )

    def _after_reconciliation_attempt_lock(self) -> None:
        """Private synchronization seam overridden only by concurrency tests."""

    def _before_active_attempt_lock(self) -> None:
        """Private synchronization seam overridden only by concurrency tests."""

    def mark_reconciliation_acknowledged(
        self,
        principal: AuthenticatedPrincipal,
        token: UUID,
        tombstone: ReconciliationTombstone,
        now: datetime,
    ) -> None:
        """Mark an exact durable ACK only after the broker accepted it."""

        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                principal_row = self._lock_principal(database, principal.principal_id)
                if principal_row.reconciliation_token != str(token):
                    raise ReversionJobLeaseLostError
                attempt = database.scalar(
                    select(ReversionAttemptRow).where(
                        ReversionAttemptRow.principal_id == str(principal.principal_id),
                        ReversionAttemptRow.create_sequence
                        == tombstone.create_sequence,
                    )
                )
                if attempt is not None:
                    proof = tombstone.proof
                    if (
                        proof.principal.principal_id != principal.principal_id
                        or attempt.attempt_id != str(proof.attempt_id)
                        or attempt.unit_id != str(proof.unit_id)
                        or attempt.proof_unit_id != str(proof.unit_id)
                        or attempt.proof_id != str(proof.proof_id)
                        or attempt.proof_principal_id
                        != str(proof.principal.principal_id)
                        or attempt.policy_revision != proof.policy_revision
                        or attempt.policy_specification
                        != tombstone.policy_specification.value
                        or attempt.proof_policy_revision != proof.policy_revision
                        or attempt.exit_evidence != proof.exit_evidence.value
                        or attempt.empty_evidence != proof.empty_evidence.value
                        or attempt.removal_evidence != proof.removal_evidence.value
                        or attempt.reconciliation_ack_intent_at is None
                    ):
                        raise ReversionJobConflictError
                    attempt.proof_acknowledged_at = attempt.proof_acknowledged_at or now
                else:
                    orphan = database.get(
                        ReversionOrphanProofRow,
                        (str(principal.principal_id), tombstone.create_sequence),
                    )
                    proof = tombstone.proof
                    if orphan is None or (
                        proof.principal.principal_id != principal.principal_id
                        or orphan.attempt_id != str(proof.attempt_id)
                        or orphan.unit_id != str(proof.unit_id)
                        or orphan.proof_id != str(proof.proof_id)
                        or orphan.policy_revision != proof.policy_revision
                        or orphan.policy_specification
                        != tombstone.policy_specification.value
                        or orphan.exit_evidence != proof.exit_evidence.value
                        or orphan.empty_evidence != proof.empty_evidence.value
                        or orphan.removal_evidence != proof.removal_evidence.value
                    ):
                        raise ReversionJobConflictError
                    orphan.acknowledged_at = orphan.acknowledged_at or now
                database.flush()
        except ReversionJobConflictError, ReversionJobLeaseLostError:
            raise
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def pending_reconciliation_acknowledgements(
        self,
        principal: AuthenticatedPrincipal,
        token: UUID,
        now: datetime,
        limit: int,
    ) -> tuple[ReconciliationTombstone, ...]:
        """Return durable exact ACK intents before any new broker page is read."""

        if type(limit) is not int or limit <= 0:
            raise ValueError("Reconciliation ACK batch limit is invalid")
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._lock_principal(database, principal.principal_id)
                if (
                    row.reconciliation_token != str(token)
                    or row.reconciliation_expires_at is None
                    or _required_utc(row.reconciliation_expires_at) < now
                ):
                    raise ReversionJobLeaseLostError
                attempts = database.scalars(
                    select(ReversionAttemptRow)
                    .where(
                        ReversionAttemptRow.principal_id == str(principal.principal_id),
                        ReversionAttemptRow.proof_id.is_not(None),
                        ReversionAttemptRow.proof_acknowledged_at.is_(None),
                    )
                    .order_by(ReversionAttemptRow.create_sequence)
                    .limit(limit)
                ).all()
                orphans = database.scalars(
                    select(ReversionOrphanProofRow)
                    .where(
                        ReversionOrphanProofRow.principal_id
                        == str(principal.principal_id),
                        ReversionOrphanProofRow.acknowledged_at.is_(None),
                    )
                    .order_by(ReversionOrphanProofRow.create_sequence)
                    .limit(limit)
                ).all()
                values: list[ReconciliationTombstone] = []
                for item in (*attempts, *orphans):
                    if isinstance(item, ReversionAttemptRow):
                        item.reconciliation_ack_intent_at = (
                            item.reconciliation_ack_intent_at or now
                        )
                    values.append(
                        ReconciliationTombstone(
                            item.create_sequence,
                            EvidenceDigest(cast(str, item.policy_specification)),
                            TerminationProof(
                                UUID(item.proof_id),
                                UUID(item.attempt_id),
                                UUID(
                                    item.proof_unit_id
                                    if isinstance(item, ReversionAttemptRow)
                                    else item.unit_id
                                ),
                                principal,
                                cast(str, item.proof_policy_revision)
                                if isinstance(item, ReversionAttemptRow)
                                else item.policy_revision,
                                EvidenceDigest(cast(str, item.exit_evidence)),
                                EvidenceDigest(cast(str, item.empty_evidence)),
                                EvidenceDigest(cast(str, item.removal_evidence)),
                            ),
                        )
                    )
                database.flush()
                return tuple(
                    sorted(values, key=lambda item: item.create_sequence)[:limit]
                )
        except ReversionJobLeaseLostError:
            raise
        except SQLAlchemyError, TypeError, ValueError:
            raise ReversionJobRepositoryError from None

    def complete_reconciliation(
        self, principal: AuthenticatedPrincipal, token: UUID, now: datetime
    ) -> None:
        """Publish readiness after a fixed-point response was durably observed."""

        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                row = self._lock_principal(database, principal.principal_id)
                if (
                    row.reconciliation_token != str(token)
                    or row.reconciliation_expires_at is None
                    or _required_utc(row.reconciliation_expires_at) < now
                ):
                    raise ReversionJobLeaseLostError
                unproven = database.scalar(
                    select(ReversionAttemptRow.attempt_id)
                    .where(
                        ReversionAttemptRow.principal_id == str(principal.principal_id),
                        ReversionAttemptRow.create_intent_at.is_not(None),
                        ReversionAttemptRow.proof_id.is_(None),
                    )
                    .limit(1)
                )
                if unproven is not None:
                    raise ReversionProofRequiredError
                if not row.reconciliation_fixed_point:
                    raise ReversionJobConflictError
                pending_attempt = database.scalar(
                    select(ReversionAttemptRow.attempt_id)
                    .where(
                        ReversionAttemptRow.principal_id == str(principal.principal_id),
                        ReversionAttemptRow.proof_id.is_not(None),
                        ReversionAttemptRow.proof_acknowledged_at.is_(None),
                    )
                    .limit(1)
                )
                pending_orphan = database.scalar(
                    select(ReversionOrphanProofRow.proof_id)
                    .where(
                        ReversionOrphanProofRow.principal_id
                        == str(principal.principal_id),
                        ReversionOrphanProofRow.acknowledged_at.is_(None),
                    )
                    .limit(1)
                )
                if pending_attempt is not None or pending_orphan is not None:
                    raise ReversionJobConflictError
                row.reconciliation_complete = True
                row.reconciliation_owner = None
                row.reconciliation_token = None
                row.reconciliation_expires_at = None
                database.flush()
        except (
            ReversionJobConflictError,
            ReversionJobLeaseLostError,
            ReversionProofRequiredError,
        ):
            raise
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
        self, now: datetime, expires_at: datetime, incomplete_before: datetime
    ) -> int:
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
                if self._engine.dialect.name == "postgresql":
                    statement = statement.with_for_update(skip_locked=True)
                recovered = 0
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
                    recovered += 1
                incomplete_result = database.execute(
                    update(ReversionJobRow)
                    .where(
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
                return recovered + int(getattr(incomplete_result, "rowcount", 0))
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

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

    def expire_terminal(
        self,
        worker_id: str,
        now: datetime,
        cleanup_lease_expires_at: datetime,
        limit: int,
    ) -> tuple[ExpiredReversionObjects, ...]:
        terminal = tuple(
            state.value
            for state in TERMINAL_REVERSION_STATES
            if state is not ReversionJobState.EXPIRED
        )
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                claimable = or_(
                    and_(
                        ReversionJobRow.state.in_(terminal),
                        ReversionJobRow.expires_at <= now,
                    ),
                    and_(
                        ReversionJobRow.state == ReversionJobState.EXPIRED.value,
                        ReversionJobRow.cleanup_completed.is_(False),
                        or_(
                            ReversionJobRow.cleanup_token.is_(None),
                            ReversionJobRow.cleanup_expires_at <= now,
                        ),
                    ),
                )
                statement = (
                    select(ReversionJobRow)
                    .where(claimable)
                    .order_by(ReversionJobRow.expires_at, ReversionJobRow.id)
                    .limit(limit)
                )
                if self._engine.dialect.name == "postgresql":
                    statement = statement.with_for_update(skip_locked=True)
                rows = tuple(database.scalars(statement))
                expired: list[ExpiredReversionObjects] = []
                for row in rows:
                    token = uuid4()
                    derived = tuple(
                        reversion_result_object_id(UUID(row.id), number)
                        for number in range(1, row.attempt + 1)
                    )
                    stored = (
                        UUID(row.result_object_id) if row.result_object_id else None
                    )
                    if stored is not None and stored not in derived:
                        derived = (*derived, stored)
                    row.state = ReversionJobState.EXPIRED.value
                    row.error_code = None
                    row.error_message = None
                    row.result_mode = None
                    row.result_object_id = None
                    row.result_sha256 = None
                    row.result_size = None
                    row.trace_metadata = None
                    row.updated_at = now
                    row.cleanup_owner = worker_id
                    row.cleanup_token = str(token)
                    row.cleanup_expires_at = cleanup_lease_expires_at
                    expired.append(
                        ExpiredReversionObjects(
                            UUID(row.id),
                            token,
                            UUID(row.owner_id),
                            UUID(row.source_object_id),
                            derived,
                        )
                    )
                database.flush()
                return tuple(expired)
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None

    def complete_cleanup(self, job_id: UUID, cleanup_token: UUID) -> bool:
        try:
            with DatabaseSession(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                result = database.execute(
                    update(ReversionJobRow)
                    .where(
                        ReversionJobRow.id == str(job_id),
                        ReversionJobRow.state == ReversionJobState.EXPIRED.value,
                        ReversionJobRow.cleanup_completed.is_(False),
                        ReversionJobRow.cleanup_token == str(cleanup_token),
                    )
                    .values(
                        cleanup_completed=True,
                        cleanup_owner=None,
                        cleanup_token=None,
                        cleanup_expires_at=None,
                    )
                )
                return getattr(result, "rowcount", 0) == 1
        except SQLAlchemyError:
            raise ReversionJobRepositoryError from None
