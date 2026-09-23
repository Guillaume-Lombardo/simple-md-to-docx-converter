"""Separate Composer runners share durable cancellation and publication state."""

import hashlib
from datetime import timedelta
from pathlib import Path
from threading import Event
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from markweave.composer.connections import ConnectionActor, ConnectionService
from markweave.composer.egress import EgressCancelledError
from markweave.http.composer_step_runner import ComposerStepRunner, ModelStepInput
from markweave.persistence.composer import (
    SqlComposerModelStepRepository,
    SqlComposerRepository,
)
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import (
    ComposerConnectionGrantRow,
    ComposerConnectionRow,
    ComposerCredentialRow,
    ComposerProposalRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.storage import FilesystemObjectStore

pytestmark = pytest.mark.integration


def _setup(tmp_path: Path) -> tuple[Engine, UUID, UUID, UUID]:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    owner_id, connection_id = uuid4(), uuid4()
    with Session(engine) as database, database.begin():
        database.add(
            UserRow(
                id=str(owner_id),
                username=str(owner_id),
                normalized_username=str(owner_id),
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
                name="Internal",
                endpoint="https://model.internal/v1",
                selected_model="small",
                permitted_models='["small"]',
                enabled=True,
                version=1,
                generation=1,
                outage=False,
            )
        )
        database.flush()
        database.add(
            ComposerConnectionGrantRow(
                connection_id=str(connection_id), user_id=str(owner_id)
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
    draft = SqlComposerRepository(
        engine, FilesystemObjectStore(tmp_path)
    ).create_draft_with_source(
        owner_id,
        b"source",
        "scanner-approved",
        title="Draft",
        content="original",
        media_type="text/markdown",
    )
    return engine, owner_id, draft.id, connection_id


def _input(connection_id: UUID) -> ModelStepInput:
    return ModelStepInput(
        connection_id=connection_id,
        if_match='"1"',
        idempotency_key="cancel-remote",
        approved_endpoint="https://model.internal/v1",
        approved_model="small",
        content="Explicitly approved text",
        max_output_tokens=8,
        payload_digest=hashlib.sha256(b"approved-call").hexdigest(),
    )


def test_another_runner_cancels_in_flight_call_before_sql_proposal_publication(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    engine, owner_id, draft_id, connection_id = _setup(tmp_path)
    repository_a = SqlComposerModelStepRepository(engine)
    repository_b = SqlComposerModelStepRepository(engine)
    connection = mocker.Mock(spec=ConnectionService)
    entered, released = Event(), Event()

    def delayed_chat(*_args: object, cancel_event: Event, **_kwargs: object) -> None:
        entered.set()
        assert cancel_event.wait(2)
        released.set()
        raise EgressCancelledError("Model call was cancelled")

    connection.chat.side_effect = delayed_chat
    runner_a = ComposerStepRunner(
        repository_a, connection, maximum_active=1, lease=timedelta(seconds=5)
    )
    runner_b = ComposerStepRunner(
        repository_b, connection, maximum_active=1, lease=timedelta(seconds=5)
    )
    actor = ConnectionActor(owner_id, is_admin=False, can_manage_personal=False)
    try:
        step = runner_a.start(actor, draft_id, _input(connection_id))
        assert entered.wait(2)
        assert runner_b.cancel(owner_id, draft_id, step.id).status == "cancelled"
        assert released.wait(2)
        assert repository_a.get_model_step(owner_id, draft_id, step.id).status == (
            "cancelled"
        )
        with Session(engine) as database:
            assert database.scalars(select(ComposerProposalRow)).all() == []
    finally:
        runner_a.close()
        runner_b.close()
        engine.dispose()
