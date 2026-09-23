"""A cancelled Composer model step never hands a late answer to publication."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.composer.connections import ConnectionActor, ConnectionService
from markweave.composer.egress import EgressCancelledError, EgressCapacityError
from markweave.composer.revisions import ComposerConflictError
from markweave.http.composer_errors import ComposerUnavailableError
from markweave.http.composer_step_runner import ComposerStepRunner, ModelStepInput
from markweave.observability import OperationalMetrics, QueueSnapshot
from markweave.persistence.composer import (
    SYSTEM_ACTOR_ID,
    ComposerModelStep,
    SqlComposerModelStepRepository,
)


def _step(state: str = "running") -> ComposerModelStep:
    now = datetime.now(UTC)
    return ComposerModelStep(
        id=UUID(int=31),
        draft_id=UUID(int=17),
        owner_id=UUID(int=2),
        base_version=3,
        connection_id=UUID(int=23),
        connection_generation=2,
        approved_endpoint="https://llm.example.test/v1",
        model="small",
        payload_digest="a" * 64,
        state=state,
        proposal_id=None,
        safe_error_code=None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=10),
    )


def _input() -> ModelStepInput:
    return ModelStepInput(
        connection_id=UUID(int=23),
        if_match='"3"',
        idempotency_key="one-call",
        approved_endpoint="https://llm.example.test/v1",
        approved_model="small",
        content="Explicitly approved text",
        max_output_tokens=8,
        payload_digest="a" * 64,
    )


@pytest.mark.unit
def test_cancelled_in_flight_result_never_reaches_proposal_gate(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=SqlComposerModelStepRepository)
    connection = mocker.Mock(spec=ConnectionService)
    step = _step()
    repository.start_model_step.return_value = (step, True)
    repository.get_model_step.return_value = step
    repository.cancel_model_step.return_value = _step("cancelled")
    entered = Event()
    completed = Event()

    def delayed_chat(*_args: object, cancel_event: Event, **_kwargs: object) -> None:
        entered.set()
        assert cancel_event.wait(1)
        completed.set()
        raise EgressCancelledError("Model call was cancelled")

    connection.chat.side_effect = delayed_chat
    runner = ComposerStepRunner(
        repository, connection, maximum_active=1, lease=timedelta(seconds=2)
    )
    actor = ConnectionActor(UUID(int=2), is_admin=False, can_manage_personal=True)

    runner.start(actor, step.draft_id, _input())
    assert entered.wait(1)
    cancelled = runner.cancel(actor.id, step.draft_id, step.id)
    assert cancelled.status == "cancelled"
    assert completed.wait(1)
    runner.close()

    repository.finish_model_step.assert_not_called()
    assert connection.chat.call_args.kwargs["max_output_tokens"] == 8
    repository.cancel_model_step.assert_any_call(actor.id, step.draft_id, step.id)


@pytest.mark.unit
def test_close_after_durable_admission_records_a_system_cancellation(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=SqlComposerModelStepRepository)
    connection = mocker.Mock(spec=ConnectionService)
    step = _step()
    runner = ComposerStepRunner(
        repository, connection, maximum_active=1, lease=timedelta(seconds=2)
    )
    actor = ConnectionActor(UUID(int=2), is_admin=False, can_manage_personal=True)

    def admit_then_close(
        *_args: object, **_kwargs: object
    ) -> tuple[ComposerModelStep, bool]:
        runner.close()
        return step, True

    repository.start_model_step.side_effect = admit_then_close
    repository.cancel_model_step.return_value = _step("cancelled")

    with pytest.raises(ComposerUnavailableError):
        runner.start(actor, step.draft_id, _input())

    repository.cancel_model_step.assert_called_once_with(
        actor.id, step.draft_id, step.id, actor_id=SYSTEM_ACTOR_ID
    )
    connection.chat.assert_not_called()


@pytest.mark.unit
def test_idempotent_retry_does_not_launch_a_second_provider_call(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=SqlComposerModelStepRepository)
    connection = mocker.Mock(spec=ConnectionService)
    step = _step("completed")
    repository.start_model_step.return_value = (step, False)
    runner = ComposerStepRunner(
        repository, connection, maximum_active=1, lease=timedelta(seconds=2)
    )
    actor = ConnectionActor(UUID(int=2), is_admin=False, can_manage_personal=True)

    returned = runner.start(actor, step.draft_id, _input())
    runner.close()

    assert returned == step
    connection.chat.assert_not_called()


@pytest.mark.unit
def test_post_admission_capacity_records_safe_failure_and_step_metrics(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=SqlComposerModelStepRepository)
    connection = mocker.Mock(spec=ConnectionService)
    step = _step()
    repository.start_model_step.return_value = (step, True)
    repository.get_model_step.return_value = step
    failed = Event()

    def fail_model_step(*_args: object, **_kwargs: object) -> ComposerModelStep:
        failed.set()
        return replace(_step("failed"), safe_error_code="capacity_exhausted")

    repository.fail_model_step.side_effect = fail_model_step
    connection.chat.side_effect = EgressCapacityError("Model connection is busy")
    metrics = OperationalMetrics()
    runner = ComposerStepRunner(
        repository,
        connection,
        maximum_active=1,
        lease=timedelta(seconds=2),
        metrics=metrics,
    )
    actor = ConnectionActor(UUID(int=2), is_admin=False, can_manage_personal=True)

    runner.start(actor, step.draft_id, _input())
    assert failed.wait(1)
    runner.close()

    repository.fail_model_step.assert_called_once_with(
        actor.id, step.draft_id, step.id, error_code="capacity_exhausted"
    )
    repository.finish_model_step.assert_not_called()
    rendered = metrics.render(QueueSnapshot(0, 0, 0))
    assert (
        'md_converter_composer_model_step_failures_total{code="capacity_exhausted"} 1'
        in rendered
    )
    assert (
        'md_converter_composer_model_step_duration_seconds_count{outcome="failed"} 1'
        in rendered
    )
    assert "md_converter_composer_active_model_steps 0" in rendered
    assert _input().content not in rendered


@pytest.mark.unit
def test_cancel_winning_finish_race_is_not_counted_as_execution_failure(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=SqlComposerModelStepRepository)
    connection = mocker.Mock(spec=ConnectionService)
    step = _step()
    state = {"step": step}
    repository.start_model_step.return_value = (step, True)
    repository.get_model_step.side_effect = lambda *_args: state["step"]
    connection.chat.return_value = {
        "choices": [{"message": {"content": "Pending review"}}]
    }
    entered_finish, release_finish = Event(), Event()

    def finish_model_step(*_args: object, **_kwargs: object) -> None:
        entered_finish.set()
        assert release_finish.wait(1)
        raise ComposerConflictError("Model step was cancelled")

    def cancel_model_step(*_args: object, **_kwargs: object) -> ComposerModelStep:
        state["step"] = _step("cancelled")
        return state["step"]

    repository.finish_model_step.side_effect = finish_model_step
    repository.cancel_model_step.side_effect = cancel_model_step
    repository.fail_model_step.side_effect = lambda *_args, **_kwargs: state["step"]
    metrics = OperationalMetrics()
    runner = ComposerStepRunner(
        repository,
        connection,
        maximum_active=1,
        lease=timedelta(seconds=2),
        metrics=metrics,
    )
    actor = ConnectionActor(UUID(int=2), is_admin=False, can_manage_personal=True)

    runner.start(actor, step.draft_id, _input())
    assert entered_finish.wait(1)
    assert runner.cancel(actor.id, step.draft_id, step.id).status == "cancelled"
    release_finish.set()
    runner.close()

    rendered = metrics.render(QueueSnapshot(0, 0, 0))
    assert "md_converter_composer_model_step_failures_total" not in rendered
    assert (
        'md_converter_composer_model_step_duration_seconds_count{outcome="cancelled"} 1'
        in rendered
    )
    repository.finish_model_step.assert_called_once()


@pytest.mark.unit
def test_close_waits_until_a_new_worker_is_started_before_joining(
    mocker: MockerFixture,
) -> None:
    repository = mocker.Mock(spec=SqlComposerModelStepRepository)
    connection = mocker.Mock(spec=ConnectionService)
    step = _step()
    repository.start_model_step.return_value = (step, True)
    repository.get_model_step.return_value = step
    repository.cancel_model_step.return_value = _step("cancelled")
    entered_start, release_start, entered_close = Event(), Event(), Event()
    original_start = Thread.start

    def gated_start(thread: Thread) -> None:
        if thread.name == "composer-model-step":
            entered_start.set()
            assert release_start.wait(1)
        original_start(thread)

    def interrupted_chat(
        *_args: object, cancel_event: Event, **_kwargs: object
    ) -> None:
        assert cancel_event.wait(1)
        raise EgressCancelledError("Model call was cancelled")

    mocker.patch.object(Thread, "start", gated_start)
    connection.chat.side_effect = interrupted_chat
    runner = ComposerStepRunner(
        repository, connection, maximum_active=1, lease=timedelta(seconds=2)
    )
    actor = ConnectionActor(UUID(int=2), is_admin=False, can_manage_personal=True)
    errors: list[Exception] = []

    def start_step() -> None:
        try:
            runner.start(actor, step.draft_id, _input())
        except Exception as error:
            errors.append(error)

    def close_runner() -> None:
        entered_close.set()
        try:
            runner.close()
        except Exception as error:
            errors.append(error)

    caller = Thread(target=start_step, name="test-start")
    caller.start()
    assert entered_start.wait(1)
    closer = Thread(target=close_runner, name="test-close")
    closer.start()
    assert entered_close.wait(1)
    release_start.set()
    caller.join(timeout=2)
    closer.join(timeout=2)

    assert not caller.is_alive()
    assert not closer.is_alive()
    assert errors == []
    repository.cancel_model_step.assert_any_call(
        actor.id, step.draft_id, step.id, actor_id=SYSTEM_ACTOR_ID
    )
