"""Broker principal reconciliation, tombstone retention and acknowledgement transactions."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.exc import SQLAlchemyError
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
from markweave.persistence.reversion_jobs.common import (
    _required_utc,
    _SqlReversionStore,
)
from markweave.persistence.schema import (
    ReversionAttemptRow,
    ReversionBrokerPrincipalRow,
    ReversionOrphanProofRow,
)
from markweave.persistence.sql import serialize_sqlite_write
from markweave.reversion_jobs.errors import (
    ReversionJobConflictError,
    ReversionJobLeaseLostError,
    ReversionJobRepositoryError,
    ReversionProofRequiredError,
)

_MAX_WORKER_ID_LENGTH = 255


class _SqlReversionReconciliation(_SqlReversionStore):
    """Broker principal reconciliation, tombstone retention and acknowledgement transactions."""

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
