"""Durable model-step admission and atomic proposal publication."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from markweave.composer.connections import ConnectionAuthorizationError
from markweave.composer.drafts import ComposerProposal
from markweave.composer.revisions import ComposerConflictError, ComposerNotFoundError
from markweave.persistence.composer.audit import SYSTEM_ACTOR_ID, record_content_audit
from markweave.persistence.composer.common import owned_draft, proposal_from_row, utc
from markweave.persistence.errors import PersistenceError
from markweave.persistence.schema import (
    ComposerConnectionGrantRow,
    ComposerConnectionRow,
    ComposerCredentialRow,
    ComposerModelStepGateRow,
    ComposerModelStepRow,
    ComposerPersonalPermissionRow,
    ComposerProposalRow,
    UserRow,
)
from markweave.persistence.sql import serialize_sqlite_write

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_MAX_IDEMPOTENCY_KEY_LENGTH = 128


class ComposerCapacityError(RuntimeError):
    """The configured global model-call admission limit is saturated."""


@dataclass(frozen=True, slots=True)
class ComposerModelStep:
    """Content-free durable attempt state for client polling and worker fences."""

    id: UUID
    draft_id: UUID
    owner_id: UUID
    base_version: int
    connection_id: UUID
    connection_generation: int
    approved_endpoint: str
    model: str
    payload_digest: str
    state: str
    proposal_id: UUID | None
    safe_error_code: str | None
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    @property
    def status(self) -> str:
        return self.state

    @property
    def model_identity(self) -> str:
        return self.model

    @property
    def error_code(self) -> str | None:
        return self.safe_error_code


def _step(row: ComposerModelStepRow) -> ComposerModelStep:
    return ComposerModelStep(
        UUID(row.id),
        UUID(row.draft_id),
        UUID(row.owner_id),
        row.base_version,
        UUID(row.connection_id),
        row.connection_generation,
        row.approved_endpoint,
        row.model,
        row.payload_digest,
        row.state,
        UUID(row.proposal_id) if row.proposal_id else None,
        row.safe_error_code,
        utc(row.created_at),
        utc(row.updated_at),
        utc(row.expires_at),
    )


def _owned_step(
    database: Session, owner_id: UUID, draft_id: UUID, step_id: UUID, *, lock: bool
) -> ComposerModelStepRow:
    statement = select(ComposerModelStepRow).where(
        ComposerModelStepRow.id == str(step_id),
        ComposerModelStepRow.draft_id == str(draft_id),
        ComposerModelStepRow.owner_id == str(owner_id),
    )
    if lock:
        statement = statement.with_for_update()
    row = database.scalar(statement)
    if row is None:
        raise ComposerNotFoundError("Composer model step does not exist")
    return row


class SqlComposerModelStepRepository:
    """SQL-only step state shared by standalone and distributed profiles."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        new_id: Callable[[], UUID] = uuid4,
        on_expiration: Callable[[int], None] | None = None,
        on_recovery: Callable[[int], None] | None = None,
    ) -> None:
        """Observe committed lease expirations and stale-step sweeps after commit."""
        self._engine = engine
        self._clock = clock
        self._new_id = new_id
        self._on_expiration = on_expiration
        self._on_recovery = on_recovery

    def start_model_step(  # noqa: PLR0912, PLR0913 - atomic admission and retry fences
        self,
        owner_id: UUID,
        draft_id: UUID,
        *,
        connection_id: UUID,
        approved_endpoint: str,
        approved_model: str,
        if_match: str,
        idempotency_key: str,
        payload_digest: str,
        max_active: int,
        lease: timedelta,
    ) -> tuple[ComposerModelStep, bool]:
        """Admit at most the configured global number of unexpired running calls."""

        if (
            not idempotency_key
            or len(idempotency_key) > _MAX_IDEMPOTENCY_KEY_LENGTH
            or _DIGEST.fullmatch(payload_digest) is None
            or not approved_endpoint
            or not approved_model
            or isinstance(max_active, bool)
            or not isinstance(max_active, int)
            or max_active <= 0
            or lease <= timedelta(0)
        ):
            raise ValueError("Composer model step admission is invalid")
        expired = False
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                gate = database.scalar(
                    select(ComposerModelStepGateRow)
                    .where(ComposerModelStepGateRow.id == 1)
                    .with_for_update()
                )
                if gate is None:
                    raise PersistenceError from None
                now = self._clock()
                draft = owned_draft(database, owner_id, draft_id, lock=True)
                existing = database.scalar(
                    select(ComposerModelStepRow)
                    .where(
                        ComposerModelStepRow.draft_id == str(draft_id),
                        ComposerModelStepRow.idempotency_key == idempotency_key,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    if not self._same_request(
                        existing,
                        connection_id=connection_id,
                        approved_endpoint=approved_endpoint,
                        approved_model=approved_model,
                        if_match=if_match,
                        payload_digest=payload_digest,
                    ):
                        raise ComposerConflictError(
                            "Model step idempotency key was reused"
                        )
                    if existing.state == "running" and utc(existing.expires_at) <= utc(
                        now
                    ):
                        self._expire_step(database, existing, now=now)
                        database.flush()
                        expired = True
                    result = _step(existing), False
                else:
                    if if_match != f'"{draft.version}"':
                        raise ComposerConflictError("Composer draft changed")
                    connection = self._connection(database, connection_id)
                    self._authorize(
                        database,
                        owner_id,
                        connection,
                        expected_role=None,
                        approved_endpoint=approved_endpoint,
                        approved_model=approved_model,
                        expected_generation=None,
                    )
                    active = database.scalar(
                        select(func.count(ComposerModelStepRow.id)).where(
                            ComposerModelStepRow.state == "running",
                            ComposerModelStepRow.expires_at > now,
                        )
                    )
                    if int(active or 0) >= max_active:
                        raise ComposerCapacityError("Model call capacity is busy")
                    user = database.get(UserRow, str(owner_id))
                    if user is None:
                        raise ConnectionAuthorizationError(
                            "Connection access is denied"
                        )
                    row = ComposerModelStepRow(
                        id=str(self._new_id()),
                        draft_id=str(draft_id),
                        owner_id=str(owner_id),
                        actor_role=user.role,
                        base_version=draft.version,
                        connection_id=str(connection_id),
                        connection_generation=connection.generation,
                        approved_endpoint=approved_endpoint,
                        model=approved_model,
                        payload_digest=payload_digest,
                        idempotency_key=idempotency_key,
                        state="running",
                        proposal_id=None,
                        safe_error_code=None,
                        created_at=now,
                        updated_at=now,
                        expires_at=now + lease,
                    )
                    database.add(row)
                    record_content_audit(
                        database,
                        event_id=self._new_id(),
                        owner_id=owner_id,
                        actor_id=owner_id,
                        operation="model_step_start",
                        target_kind="composer_model_step",
                        target_id=UUID(row.id),
                        draft_id=draft_id,
                        draft_version=draft.version,
                        created_at=now,
                    )
                    database.flush()
                    result = _step(row), True
            if expired:
                self._record_expiration(1)
            return result
        except (
            ComposerConflictError,
            ComposerNotFoundError,
            ConnectionAuthorizationError,
        ):
            raise
        except IntegrityError:
            raise ComposerConflictError("Model step changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def get_model_step(
        self, owner_id: UUID, draft_id: UUID, step_id: UUID
    ) -> ComposerModelStep:
        try:
            with Session(self._engine) as database:
                owned_draft(database, owner_id, draft_id)
                current = _step(
                    _owned_step(database, owner_id, draft_id, step_id, lock=False)
                )
            if current.state != "running" or current.expires_at > utc(self._clock()):
                return current
            expired = False
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                owned_draft(database, owner_id, draft_id, lock=True)
                row = _owned_step(database, owner_id, draft_id, step_id, lock=True)
                now = self._clock()
                if row.state == "running" and utc(row.expires_at) <= utc(now):
                    self._expire_step(database, row, now=now)
                    database.flush()
                    expired = True
                result = _step(row)
            if expired:
                self._record_expiration(1)
            return result
        except SQLAlchemyError:
            raise PersistenceError from None

    def cancel_model_step(
        self,
        owner_id: UUID,
        draft_id: UUID,
        step_id: UUID,
        *,
        actor_id: UUID | None = None,
    ) -> ComposerModelStep:
        return self._terminalize(
            owner_id,
            draft_id,
            step_id,
            state="cancelled",
            error_code=None,
            actor_id=owner_id if actor_id is None else actor_id,
        )

    def fail_model_step(
        self, owner_id: UUID, draft_id: UUID, step_id: UUID, *, error_code: str
    ) -> ComposerModelStep:
        if _SAFE_ERROR_CODE.fullmatch(error_code) is None:
            raise ValueError("Model step error code is invalid")
        return self._terminalize(
            owner_id,
            draft_id,
            step_id,
            state="failed",
            error_code=error_code,
            actor_id=SYSTEM_ACTOR_ID,
        )

    def finish_model_step(
        self,
        owner_id: UUID,
        draft_id: UUID,
        step_id: UUID,
        *,
        proposed_value: str,
        provenance: str,
    ) -> ComposerProposal:
        """Publish one pending proposal only if every start-time fence still holds."""

        now = self._clock()
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                draft = owned_draft(database, owner_id, draft_id, lock=True)
                row = _owned_step(database, owner_id, draft_id, step_id, lock=True)
                if row.state == "completed" and row.proposal_id:
                    prior = database.get(ComposerProposalRow, row.proposal_id)
                    if (
                        prior is not None
                        and prior.proposed_value == proposed_value
                        and prior.provenance == provenance
                    ):
                        return proposal_from_row(prior)
                    raise ComposerConflictError("Model step result changed")
                if row.state != "running" or utc(row.expires_at) <= utc(now):
                    raise ComposerConflictError("Model step is no longer active")
                if draft.version != row.base_version:
                    raise ComposerConflictError("Composer draft changed")
                connection = self._connection(database, UUID(row.connection_id))
                self._authorize(
                    database,
                    owner_id,
                    connection,
                    expected_role=row.actor_role,
                    approved_endpoint=row.approved_endpoint,
                    approved_model=row.model,
                    expected_generation=row.connection_generation,
                )
                proposal = ComposerProposalRow(
                    id=str(self._new_id()),
                    draft_id=str(draft_id),
                    base_version=row.base_version,
                    state="pending",
                    proposed_value=proposed_value,
                    decided_value=None,
                    provenance=provenance,
                    created_at=now,
                    decided_at=None,
                    decided_by=None,
                )
                database.add(proposal)
                row.state = "completed"
                row.proposal_id = proposal.id
                row.updated_at = now
                draft.version += 1
                draft.updated_at = now
                for operation, target_kind, target_id in (
                    ("model_step_complete", "composer_model_step", step_id),
                    ("proposal_create", "composer_proposal", UUID(proposal.id)),
                ):
                    record_content_audit(
                        database,
                        event_id=self._new_id(),
                        owner_id=owner_id,
                        actor_id=SYSTEM_ACTOR_ID,
                        operation=operation,
                        target_kind=target_kind,
                        target_id=target_id,
                        draft_id=draft_id,
                        draft_version=draft.version,
                        created_at=now,
                    )
                database.flush()
                return proposal_from_row(proposal)
        except (
            ComposerConflictError,
            ComposerNotFoundError,
            ConnectionAuthorizationError,
        ):
            raise
        except IntegrityError:
            raise ComposerConflictError("Model step changed") from None
        except SQLAlchemyError:
            raise PersistenceError from None

    def recover_stale_model_steps(self, *, stale_before: datetime, limit: int) -> int:
        """End one bounded batch; recovery and expiration count committed rows."""

        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("Model step recovery limit must be positive")

        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                now = self._clock()
                candidates = database.scalars(
                    select(ComposerModelStepRow.id)
                    .where(
                        ComposerModelStepRow.state == "running",
                        ComposerModelStepRow.expires_at <= stale_before,
                    )
                    .order_by(ComposerModelStepRow.expires_at, ComposerModelStepRow.id)
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                ).all()
                if not candidates:
                    return 0
                updated = database.connection().execute(
                    update(ComposerModelStepRow)
                    .where(
                        ComposerModelStepRow.id.in_(candidates),
                        ComposerModelStepRow.state == "running",
                        ComposerModelStepRow.expires_at <= stale_before,
                    )
                    .values(
                        state="failed",
                        safe_error_code="step_expired",
                        updated_at=now,
                    )
                    .returning(
                        ComposerModelStepRow.id,
                        ComposerModelStepRow.owner_id,
                        ComposerModelStepRow.draft_id,
                        ComposerModelStepRow.base_version,
                    )
                )
                changed = tuple(updated)
                for row in changed:
                    self._audit_expiration(
                        database,
                        step_id=row.id,
                        owner_id=row.owner_id,
                        draft_id=row.draft_id,
                        base_version=row.base_version,
                        now=now,
                    )
                count = len(changed)
            self._record_expiration(count)
            if count and self._on_recovery is not None:
                self._on_recovery(count)
            return count
        except SQLAlchemyError:
            raise PersistenceError from None

    def _expire_step(
        self, database: Session, row: ComposerModelStepRow, *, now: datetime
    ) -> None:
        row.state = "failed"
        row.safe_error_code = "step_expired"
        row.updated_at = now
        self._audit_expiration(
            database,
            step_id=row.id,
            owner_id=row.owner_id,
            draft_id=row.draft_id,
            base_version=row.base_version,
            now=now,
        )

    def _audit_expiration(  # noqa: PLR0913 - audited transition identity
        self,
        database: Session,
        *,
        step_id: str,
        owner_id: str,
        draft_id: str,
        base_version: int,
        now: datetime,
    ) -> None:
        record_content_audit(
            database,
            event_id=self._new_id(),
            owner_id=UUID(owner_id),
            actor_id=SYSTEM_ACTOR_ID,
            operation="model_step_expire",
            target_kind="composer_model_step",
            target_id=UUID(step_id),
            draft_id=UUID(draft_id),
            draft_version=base_version,
            created_at=now,
        )

    def _record_expiration(self, count: int) -> None:
        if count and self._on_expiration is not None:
            self._on_expiration(count)

    def _terminalize(  # noqa: PLR0913 - explicit terminal transition identity
        self,
        owner_id: UUID,
        draft_id: UUID,
        step_id: UUID,
        *,
        state: str,
        error_code: str | None,
        actor_id: UUID,
    ) -> ComposerModelStep:
        try:
            with Session(self._engine) as database, database.begin():
                serialize_sqlite_write(database, self._engine)
                owned_draft(database, owner_id, draft_id)
                row = _owned_step(database, owner_id, draft_id, step_id, lock=True)
                if row.state == "running":
                    row.state = state
                    row.safe_error_code = error_code
                    row.updated_at = self._clock()
                    record_content_audit(
                        database,
                        event_id=self._new_id(),
                        owner_id=owner_id,
                        actor_id=actor_id,
                        operation=(
                            "model_step_cancel"
                            if state == "cancelled"
                            else "model_step_fail"
                        ),
                        target_kind="composer_model_step",
                        target_id=step_id,
                        draft_id=draft_id,
                        draft_version=row.base_version,
                        created_at=row.updated_at,
                    )
                    database.flush()
                return _step(row)
        except SQLAlchemyError:
            raise PersistenceError from None

    @staticmethod
    def _same_request(  # noqa: PLR0913 - persisted idempotency identity
        row: ComposerModelStepRow,
        *,
        connection_id: UUID,
        approved_endpoint: str,
        approved_model: str,
        if_match: str,
        payload_digest: str,
    ) -> bool:
        return (
            row.connection_id == str(connection_id)
            and row.approved_endpoint == approved_endpoint
            and row.model == approved_model
            and if_match == f'"{row.base_version}"'
            and row.payload_digest == payload_digest
        )

    @staticmethod
    def _connection(database: Session, connection_id: UUID) -> ComposerConnectionRow:
        row = database.scalar(
            select(ComposerConnectionRow)
            .where(ComposerConnectionRow.id == str(connection_id))
            .with_for_update()
        )
        if row is None:
            raise ComposerConflictError("Connection changed during model step")
        return row

    @staticmethod
    def _authorize(  # noqa: PLR0913 - one commit-time authorization fence
        database: Session,
        owner_id: UUID,
        connection: ComposerConnectionRow,
        *,
        expected_role: str | None,
        approved_endpoint: str,
        approved_model: str,
        expected_generation: int | None,
    ) -> None:
        user = database.scalar(
            select(UserRow).where(UserRow.id == str(owner_id)).with_for_update()
        )
        if (
            user is None
            or not user.active
            or user.role not in {"admin", "user"}
            or (expected_role is not None and user.role != expected_role)
        ):
            raise ConnectionAuthorizationError("Connection access is denied")
        if connection.scope == "personal":
            permission = database.scalar(
                select(ComposerPersonalPermissionRow)
                .where(ComposerPersonalPermissionRow.user_id == str(owner_id))
                .with_for_update()
            )
            if connection.owner_id != str(owner_id) or not (
                permission and permission.enabled
            ):
                raise ConnectionAuthorizationError("Connection access is denied")
        elif connection.scope == "instance":
            grant = database.scalar(
                select(ComposerConnectionGrantRow).where(
                    ComposerConnectionGrantRow.connection_id == connection.id,
                    ComposerConnectionGrantRow.user_id == str(owner_id),
                )
            )
            if grant is None:
                raise ConnectionAuthorizationError("Connection access is denied")
        else:
            raise ConnectionAuthorizationError("Connection access is denied")
        if (
            not connection.enabled
            or connection.endpoint != approved_endpoint
            or connection.selected_model != approved_model
            or (
                expected_generation is not None
                and connection.generation != expected_generation
            )
            or approved_model not in json.loads(connection.permitted_models)
        ):
            raise ComposerConflictError("Connection changed during model step")
        credential_user_id = (
            str(owner_id) if connection.identity_mode == "individual" else None
        )
        credential = database.scalar(
            select(ComposerCredentialRow).where(
                ComposerCredentialRow.connection_id == connection.id,
                ComposerCredentialRow.user_id == credential_user_id,
            )
        )
        if credential is None or not (
            credential.api_key
            or (credential.client_certificate and credential.client_private_key)
        ):
            raise ComposerConflictError("Connection credential changed")
