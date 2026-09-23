"""SQLite/files Composer model-step admission and publication fences."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import Engine, delete, select, update
from sqlalchemy.orm import Session

from markweave.composer.connections import ConnectionAuthorizationError
from markweave.composer.revisions import ComposerConflictError, ComposerNotFoundError
from markweave.persistence.composer import (
    SYSTEM_ACTOR_ID,
    ComposerCapacityError,
    SqlComposerAuditRepository,
    SqlComposerModelStepRepository,
    SqlComposerRepository,
)
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import (
    ComposerConnectionGrantRow,
    ComposerConnectionRow,
    ComposerCredentialRow,
    ComposerModelStepRow,
    ComposerPersonalPermissionRow,
    ComposerProposalRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.recovery_adapters import RecoveryDeadline, StandaloneRecoveryAdapter
from markweave.retention import (
    DataRetentionPolicy,
    RetentionRepository,
    RetentionService,
)
from markweave.storage import FilesystemObjectStore, ObjectStore

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]

_PAYLOAD = hashlib.sha256(b"approved exact request").hexdigest()
_ENDPOINT = "https://model.internal/v1"
_MODEL = "approved-model"


def _setup(
    tmp_path: Path,
) -> tuple[
    Engine, SqlComposerRepository, SqlComposerModelStepRepository, UUID, UUID, UUID
]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    owner, other, connection_id = uuid4(), uuid4(), uuid4()
    with Session(engine) as database, database.begin():
        for user_id in (owner, other):
            database.add(
                UserRow(
                    id=str(user_id),
                    username=str(user_id),
                    normalized_username=str(user_id),
                    password_hash="hash",  # noqa: S106 - isolated fixture
                    role="user",
                    active=True,
                    auth_version=0,
                    password_change_required=False,
                )
            )
        database.flush()
        database.add(
            ComposerConnectionRow(
                id=str(connection_id),
                scope="instance",
                owner_id=None,
                identity_mode="shared",
                name="Test",
                endpoint=_ENDPOINT,
                selected_model=_MODEL,
                permitted_models='["approved-model"]',
                enabled=True,
                version=1,
                generation=1,
                outage=False,
            )
        )
        database.flush()
        database.add(
            ComposerConnectionGrantRow(
                connection_id=str(connection_id), user_id=str(owner)
            )
        )
        database.add(
            ComposerCredentialRow(
                id=str(uuid4()),
                connection_id=str(connection_id),
                user_id=None,
                api_key=b"encrypted-key",
                client_certificate=None,
                client_private_key=None,
                ca_bundle=None,
                outage=False,
            )
        )
    drafts = SqlComposerRepository(engine, FilesystemObjectStore(tmp_path))
    steps = SqlComposerModelStepRepository(engine)
    return engine, drafts, steps, owner, other, connection_id


def _draft(drafts: SqlComposerRepository, owner: UUID):
    return drafts.create_draft_with_source(
        owner,
        b"source",
        "scanner-approved",
        title="Draft",
        content="original",
        media_type="text/markdown",
    )


def _start(  # noqa: PLR0913 - explicit test admission inputs
    steps: SqlComposerModelStepRepository,
    owner: UUID,
    draft_id: UUID,
    connection_id: UUID,
    *,
    etag: str = '"1"',
    key: str = "step-1",
    max_active: int = 2,
):
    return steps.start_model_step(
        owner,
        draft_id,
        connection_id=connection_id,
        approved_endpoint=_ENDPOINT,
        approved_model=_MODEL,
        if_match=etag,
        idempotency_key=key,
        payload_digest=_PAYLOAD,
        max_active=max_active,
        lease=timedelta(minutes=5),
    )


def test_start_is_durable_idempotent_globally_bounded_and_owner_scoped(
    tmp_path: Path,
) -> None:
    engine, drafts, steps, owner, other, connection_id = _setup(tmp_path)
    try:
        first = _draft(drafts, owner)
        second = _draft(drafts, owner)
        step, created = _start(steps, owner, first.id, connection_id, max_active=1)
        assert created and step.state == "running"
        assert _start(steps, owner, first.id, connection_id, max_active=1) == (
            step,
            False,
        )
        restarted = SqlComposerModelStepRepository(engine)
        assert restarted.get_model_step(owner, first.id, step.id) == step
        with pytest.raises(ComposerNotFoundError):
            restarted.get_model_step(other, first.id, step.id)
        audit_before_capacity = SqlComposerAuditRepository(engine).list_content_audit(
            owner
        )
        with pytest.raises(ComposerCapacityError, match="capacity"):
            _start(steps, owner, second.id, connection_id, key="step-2", max_active=1)
        assert (
            SqlComposerAuditRepository(engine).list_content_audit(owner)
            == audit_before_capacity
        )
        with Session(engine) as database:
            assert (
                database.scalars(
                    select(ComposerModelStepRow).where(
                        ComposerModelStepRow.draft_id == str(second.id)
                    )
                ).all()
                == []
            )
        with pytest.raises(ComposerConflictError, match="reused"):
            _start(steps, owner, first.id, connection_id, etag='"2"', max_active=1)
        cancelled = restarted.cancel_model_step(owner, first.id, step.id)
        assert cancelled.state == "cancelled"
        assert restarted.cancel_model_step(owner, first.id, step.id) == cancelled
        audit = SqlComposerAuditRepository(engine).list_content_audit(owner)
        assert sum(event.operation == "model_step_start" for event in audit) == 1
        assert sum(event.operation == "model_step_cancel" for event in audit) == 1
        automated, _ = _start(
            steps, owner, second.id, connection_id, key="step-2", max_active=1
        )
        restarted.cancel_model_step(
            owner, second.id, automated.id, actor_id=SYSTEM_ACTOR_ID
        )
        automated_audit = next(
            event
            for event in SqlComposerAuditRepository(engine).list_content_audit(owner)
            if event.operation == "model_step_cancel"
            and event.target_id == automated.id
        )
        assert automated_audit.actor_id == SYSTEM_ACTOR_ID
    finally:
        engine.dispose()


def test_cancel_before_commit_and_human_edit_cannot_publish_late_proposal(
    tmp_path: Path,
) -> None:
    engine, drafts, steps, owner, _other, connection_id = _setup(tmp_path)
    try:
        draft = _draft(drafts, owner)
        step, _ = _start(steps, owner, draft.id, connection_id)
        steps.cancel_model_step(owner, draft.id, step.id)
        with pytest.raises(ComposerConflictError, match="no longer active"):
            steps.finish_model_step(
                owner, draft.id, step.id, proposed_value="late", provenance="model"
            )
        with Session(engine) as database:
            assert database.scalars(select(ComposerProposalRow)).all() == []
        second, _ = _start(steps, owner, draft.id, connection_id, key="second")
        drafts.save_draft(
            owner, draft.id, if_match=draft.etag, title="Draft", content="human edit"
        )
        with pytest.raises(ComposerConflictError, match="draft changed"):
            steps.finish_model_step(
                owner, draft.id, second.id, proposed_value="late", provenance="model"
            )
        with Session(engine) as database:
            assert database.scalars(select(ComposerProposalRow)).all() == []
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "mutation",
    [
        "grant",
        "generation",
        "credential",
        "user_active",
        "user_role",
        "endpoint",
        "model",
    ],
)
def test_commit_rechecks_live_authority_and_connection(
    tmp_path: Path, mutation: str
) -> None:
    engine, drafts, steps, owner, _other, connection_id = _setup(tmp_path)
    try:
        draft = _draft(drafts, owner)
        step, _ = _start(steps, owner, draft.id, connection_id)
        with Session(engine) as database, database.begin():
            if mutation == "grant":
                database.execute(delete(ComposerConnectionGrantRow))
            elif mutation == "generation":
                database.execute(update(ComposerConnectionRow).values(generation=2))
            elif mutation == "credential":
                database.execute(delete(ComposerCredentialRow))
            elif mutation == "user_active":
                database.execute(
                    update(UserRow).where(UserRow.id == str(owner)).values(active=False)
                )
            elif mutation == "user_role":
                database.execute(
                    update(UserRow).where(UserRow.id == str(owner)).values(role="admin")
                )
            elif mutation == "endpoint":
                database.execute(
                    update(ComposerConnectionRow).values(
                        endpoint="https://changed.internal/v1"
                    )
                )
            else:
                database.execute(
                    update(ComposerConnectionRow).values(selected_model="other-model")
                )
        with pytest.raises((ComposerConflictError, ConnectionAuthorizationError)):
            steps.finish_model_step(
                owner, draft.id, step.id, proposed_value="late", provenance="model"
            )
        with Session(engine) as database:
            assert database.scalars(select(ComposerProposalRow)).all() == []
    finally:
        engine.dispose()


def test_completion_is_atomic_and_restart_recovery_is_terminal(tmp_path: Path) -> None:
    engine, drafts, steps, owner, _other, connection_id = _setup(tmp_path)
    try:
        draft = _draft(drafts, owner)
        step, _ = _start(steps, owner, draft.id, connection_id)
        proposal = steps.finish_model_step(
            owner, draft.id, step.id, proposed_value="proposal", provenance="model"
        )
        assert proposal.state == "pending"
        assert steps.get_model_step(owner, draft.id, step.id).proposal_id == proposal.id
        assert (
            steps.finish_model_step(
                owner, draft.id, step.id, proposed_value="proposal", provenance="model"
            )
            == proposal
        )
        assert steps.cancel_model_step(owner, draft.id, step.id).state == "completed"
        fresh = drafts.get_draft(owner, draft.id)
        abandoned, _ = _start(
            steps, owner, draft.id, connection_id, etag=fresh.etag, key="abandoned"
        )
        with Session(engine) as database, database.begin():
            database.execute(
                update(ComposerModelStepRow)
                .where(ComposerModelStepRow.id == str(abandoned.id))
                .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
            )
        restarted = SqlComposerModelStepRepository(engine)
        assert (
            restarted.recover_stale_model_steps(stale_before=datetime.now(UTC), limit=1)
            == 1
        )
        assert restarted.get_model_step(owner, draft.id, abandoned.id).state == "failed"
        assert (
            restarted.fail_model_step(
                owner, draft.id, abandoned.id, error_code="timeout"
            ).safe_error_code
            == "step_expired"
        )
        audit = SqlComposerAuditRepository(engine).list_content_audit(owner)
        expiration = next(
            event for event in audit if event.operation == "model_step_expire"
        )
        assert expiration.target_id == abandoned.id
        assert expiration.actor_id == SYSTEM_ACTOR_ID
        assert sum(event.operation == "model_step_complete" for event in audit) == 1
        with pytest.raises(ComposerConflictError):
            restarted.finish_model_step(
                owner, draft.id, abandoned.id, proposed_value="late", provenance="model"
            )
    finally:
        engine.dispose()


def test_restarted_future_lease_expires_through_bounded_periodic_maintenance(  # noqa: PLR0915 - restart and maintenance lifecycle
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, drafts, _steps, owner, _other, connection_id = _setup(tmp_path)
    now = [datetime(2026, 9, 23, 12, tzinfo=UTC)]
    counted: list[int] = []
    recovered: list[int] = []
    try:
        initial = SqlComposerModelStepRepository(engine, clock=lambda: now[0])
        drafts_and_steps = []
        for number in range(4):
            draft = _draft(drafts, owner)
            step, created = _start(
                initial,
                owner,
                draft.id,
                connection_id,
                key=f"future-{number}",
                max_active=4,
            )
            assert created
            drafts_and_steps.append((draft, step))
        restarted = SqlComposerModelStepRepository(
            engine,
            clock=lambda: now[0],
            on_expiration=counted.append,
            on_recovery=recovered.append,
        )
        repository = mocker.Mock(spec=RetentionRepository)
        repository.claim_template_versions.return_value = ()
        repository.cleanup_audits.return_value = 0
        maintenance = RetentionService(
            repository,
            mocker.Mock(spec=ObjectStore),
            DataRetentionPolicy(86_400, 86_400, 10, 30),
            composer_model_steps=restarted,
            clock=lambda: now[0],
        )
        assert maintenance.cleanup(limit=1) == 0
        assert recovered == []
        assert all(
            restarted.get_model_step(owner, draft.id, step.id).state == "running"
            for draft, step in drafts_and_steps
        )
        now[0] += timedelta(minutes=6)
        assert maintenance.cleanup(limit=1) == 1
        assert counted == [1]
        with Session(engine) as database:
            assert (
                len(
                    database.scalars(
                        select(ComposerModelStepRow).where(
                            ComposerModelStepRow.state == "running"
                        )
                    ).all()
                )
                == 3
            )
        assert maintenance.cleanup(limit=1) == 1
        assert maintenance.cleanup(limit=1) == 1
        assert counted == [1, 1, 1]
        assert recovered == [1, 1, 1]
        # An owner read transitions the remaining row immediately.
        with Session(engine) as database:
            remaining_id = database.scalar(
                select(ComposerModelStepRow.id).where(
                    ComposerModelStepRow.state == "running"
                )
            )
        assert remaining_id is not None
        last_draft, last_step = next(
            (draft, step)
            for draft, step in drafts_and_steps
            if str(step.id) == remaining_id
        )
        assert (
            restarted.get_model_step(owner, last_draft.id, last_step.id).state
            == "failed"
        )
        assert counted == [1, 1, 1, 1]
        assert recovered == [1, 1, 1]
        assert maintenance.cleanup(limit=1) == 0
        assert counted == [1, 1, 1, 1]
        assert recovered == [1, 1, 1]
        assert all(
            restarted.get_model_step(owner, draft.id, step.id).state == "failed"
            for draft, step in drafts_and_steps
        )
        audit = SqlComposerAuditRepository(engine).list_content_audit(owner)
        expirations = [
            event for event in audit if event.operation == "model_step_expire"
        ]
        assert len(expirations) == 4
        assert all(event.actor_id == SYSTEM_ACTOR_ID for event in expirations)
        with pytest.raises(ComposerConflictError, match="no longer active"):
            draft, step = drafts_and_steps[0]
            restarted.finish_model_step(
                owner,
                draft.id,
                step.id,
                proposed_value="late",
                provenance="model",
            )
        with Session(engine) as database:
            assert database.scalars(select(ComposerProposalRow)).all() == []
    finally:
        engine.dispose()


def test_expired_owner_read_and_periodic_sweep_race_once(tmp_path: Path) -> None:
    engine, drafts, _steps, owner, _other, connection_id = _setup(tmp_path)
    now = [datetime(2026, 9, 23, 12, tzinfo=UTC)]
    expirations: list[int] = []
    recoveries: list[int] = []
    try:
        initial = SqlComposerModelStepRepository(engine, clock=lambda: now[0])
        draft = _draft(drafts, owner)
        step, _ = _start(initial, owner, draft.id, connection_id)
        now[0] += timedelta(minutes=6)
        reader = SqlComposerModelStepRepository(
            engine, clock=lambda: now[0], on_expiration=expirations.append
        )
        sweeper = SqlComposerModelStepRepository(
            engine,
            clock=lambda: now[0],
            on_expiration=expirations.append,
            on_recovery=recoveries.append,
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            read = pool.submit(reader.get_model_step, owner, draft.id, step.id)
            sweep = pool.submit(
                sweeper.recover_stale_model_steps, stale_before=now[0], limit=1
            )
            assert read.result().state == "failed"
            assert sweep.result() in (0, 1)
        assert expirations == [1]
        assert recoveries in ([], [1])
        audit = SqlComposerAuditRepository(engine).list_content_audit(owner)
        assert sum(event.operation == "model_step_expire" for event in audit) == 1
    finally:
        engine.dispose()


def test_expired_idempotency_retry_returns_terminal_step_once(tmp_path: Path) -> None:
    engine, drafts, _steps, owner, _other, connection_id = _setup(tmp_path)
    now = [datetime(2026, 9, 23, 12, tzinfo=UTC)]
    expirations: list[int] = []
    try:
        initial = SqlComposerModelStepRepository(engine, clock=lambda: now[0])
        draft = _draft(drafts, owner)
        step, created = _start(initial, owner, draft.id, connection_id)
        assert created
        now[0] += timedelta(minutes=6)
        replicas = tuple(
            SqlComposerModelStepRepository(
                engine, clock=lambda: now[0], on_expiration=expirations.append
            )
            for _ in range(2)
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            retries = tuple(
                pool.map(
                    lambda repository: _start(
                        repository, owner, draft.id, connection_id
                    ),
                    replicas,
                )
            )
        assert all(
            not newly_created
            and repeated.id == step.id
            and repeated.state == "failed"
            and repeated.safe_error_code == "step_expired"
            for repeated, newly_created in retries
        )
        assert expirations == [1]
        assert _start(replicas[0], owner, draft.id, connection_id) == (
            retries[0][0],
            False,
        )
        assert expirations == [1]
        audit = SqlComposerAuditRepository(engine).list_content_audit(owner)
        assert sum(event.operation == "model_step_start" for event in audit) == 1
        expired_audit = [
            event for event in audit if event.operation == "model_step_expire"
        ]
        assert len(expired_audit) == 1
        assert expired_audit[0].actor_id == SYSTEM_ACTOR_ID
        with pytest.raises(ComposerConflictError, match="no longer active"):
            replicas[0].finish_model_step(
                owner,
                draft.id,
                step.id,
                proposed_value="late",
                provenance="model",
            )
        with Session(engine) as database:
            assert database.scalars(select(ComposerProposalRow)).all() == []
    finally:
        engine.dispose()


def test_personal_capability_revocation_blocks_delayed_result(tmp_path: Path) -> None:
    engine, drafts, steps, owner, _other, connection_id = _setup(tmp_path)
    try:
        with Session(engine) as database, database.begin():
            database.execute(
                update(ComposerConnectionRow)
                .where(ComposerConnectionRow.id == str(connection_id))
                .values(
                    scope="personal", owner_id=str(owner), identity_mode="individual"
                )
            )
            database.execute(delete(ComposerConnectionGrantRow))
            database.execute(
                update(ComposerCredentialRow)
                .where(ComposerCredentialRow.connection_id == str(connection_id))
                .values(user_id=str(owner))
            )
            database.add(
                ComposerPersonalPermissionRow(
                    user_id=str(owner), enabled=True, version=1
                )
            )
        draft = _draft(drafts, owner)
        step, _ = _start(steps, owner, draft.id, connection_id)
        with Session(engine) as database, database.begin():
            database.execute(
                update(ComposerPersonalPermissionRow)
                .where(ComposerPersonalPermissionRow.user_id == str(owner))
                .values(enabled=False, version=2)
            )
        with pytest.raises(ConnectionAuthorizationError):
            steps.finish_model_step(
                owner, draft.id, step.id, proposed_value="late", provenance="model"
            )
        with Session(engine) as database:
            assert database.scalars(select(ComposerProposalRow)).all() == []
    finally:
        engine.dispose()


def test_standalone_backup_restores_cancelled_step_without_result(
    tmp_path: Path,
) -> None:
    source = (tmp_path / "source").resolve()
    source.mkdir()
    engine, drafts, steps, owner, _other, connection_id = _setup(source)
    try:
        draft = _draft(drafts, owner)
        step, _ = _start(steps, owner, draft.id, connection_id)
        steps.cancel_model_step(owner, draft.id, step.id)
    finally:
        engine.dispose()
    staging = (tmp_path / "backup").resolve()
    staging.mkdir()
    adapter = StandaloneRecoveryAdapter()
    database_backup, object_backup = adapter.backup(
        source, staging, RecoveryDeadline.after(10)
    )
    target = (tmp_path / "restored").resolve()
    adapter.restore(
        staging,
        target,
        (*database_backup.members, *object_backup.members),
        RecoveryDeadline.after(10),
    )
    restored_engine = create_database_engine(
        f"sqlite+pysqlite:///{target / 'metadata.sqlite3'}"
    )
    try:
        restored = SqlComposerModelStepRepository(restored_engine)
        assert restored.get_model_step(owner, draft.id, step.id).state == "cancelled"
        with pytest.raises(ComposerConflictError):
            restored.finish_model_step(
                owner, draft.id, step.id, proposed_value="late", provenance="model"
            )
        with Session(restored_engine) as database:
            assert database.scalars(select(ComposerProposalRow)).all() == []
    finally:
        restored_engine.dispose()


def test_cancel_and_finish_race_has_one_durable_outcome(tmp_path: Path) -> None:
    engine, drafts, steps, owner, _other, connection_id = _setup(tmp_path)
    try:
        draft = _draft(drafts, owner)
        step, _ = _start(steps, owner, draft.id, connection_id)

        def finish() -> str:
            try:
                steps.finish_model_step(
                    owner, draft.id, step.id, proposed_value="model", provenance="model"
                )
                return "completed"
            except ComposerConflictError:
                return "cancelled"

        with ThreadPoolExecutor(max_workers=2) as pool:
            finish_result = pool.submit(finish)
            cancel_result = pool.submit(
                steps.cancel_model_step, owner, draft.id, step.id
            )
            assert finish_result.result() in {"completed", "cancelled"}
            assert cancel_result.result().state in {"completed", "cancelled"}
        persisted = steps.get_model_step(owner, draft.id, step.id)
        with Session(engine) as database:
            proposals = tuple(database.scalars(select(ComposerProposalRow)))
        assert len(proposals) == int(persisted.state == "completed")
        if persisted.state == "completed":
            assert str(persisted.proposal_id) == proposals[0].id
        else:
            assert persisted.proposal_id is None
    finally:
        engine.dispose()
