"""Composer model steps expose only owner-scoped state and approved transmission."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.config import Settings
from markweave.http.composer_step_runner import ComposerStepRunner, ModelStepInput
from markweave.persistence.composer import (
    ComposerCapacityError,
    ComposerModelStep,
    SqlComposerModelStepRepository,
)
from markweave.persistence.composer.questions import ComposerQuestion
from tests.settings import template_settings


def _step() -> ComposerModelStep:
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
        state="running",
        proposal_id=None,
        safe_error_code=None,
        created_at=now,
        updated_at=now,
        expires_at=now + timedelta(seconds=10),
    )


def _client(
    mocker: MockerFixture, *, enabled: bool = True
) -> tuple[TestClient, Any, Any]:
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-" + "password",
        storage_profile="standalone",
        standalone_data_directory="/data",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        composer_retry_after_seconds=7,
        job_result_retention_seconds=3600,
        composer_http_request_max_bytes=4096,
        composer_maximum_request_bytes=1024,
        composer_maximum_output_tokens=32,
    )
    owner = User(UUID(int=2), "owner", "owner", "hash", Role.USER)
    authentication = mocker.Mock(spec=AuthenticationService)
    authentication.bootstrap_admin.return_value = owner
    authentication.authenticate.return_value = owner
    runner = mocker.Mock(spec=ComposerStepRunner)
    repository = mocker.Mock(spec=SqlComposerModelStepRepository)
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=mocker.Mock(),
            jobs=mocker.Mock(),
            composer_model_step_repository=repository,
            composer_steps=runner if enabled else None,
        ),
    )
    return TestClient(app, base_url="https://testserver"), runner, repository


@pytest.mark.unit
def test_start_binds_approved_endpoint_model_and_exact_text_without_echo(
    mocker: MockerFixture,
) -> None:
    client, runner, _repository = _client(mocker)
    step = _step()
    runner.start.return_value = step
    sensitive = "private-" + "document-excerpt"

    with client:
        response = client.post(
            f"/api/v1/composer/drafts/{step.draft_id}/model-steps",
            json={
                "connection_id": str(step.connection_id),
                "approved_endpoint": step.approved_endpoint,
                "approved_model": step.model,
                "content": sensitive,
                "max_output_tokens": 8,
            },
            headers={
                "If-Match": '"3"',
                "Idempotency-Key": "model-step-1",
                "X-CSRF-Token": "csrf",
            },
        )

    assert response.status_code == 202
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json()["status"] == "running"
    assert sensitive not in response.text
    actor, draft_id, input_ = runner.start.call_args.args
    assert actor.id == step.owner_id
    assert draft_id == step.draft_id
    assert isinstance(input_, ModelStepInput)
    assert input_.content == sensitive
    assert input_.approved_endpoint == step.approved_endpoint
    assert input_.approved_model == step.model
    assert len(input_.payload_digest) == 64


@pytest.mark.unit
def test_status_and_cancel_use_durable_owner_scope_when_model_is_disabled(
    mocker: MockerFixture,
) -> None:
    client, _runner, repository = _client(mocker, enabled=False)
    step = _step()
    repository.get_model_step.return_value = step
    repository.cancel_model_step.return_value = step

    with client:
        status = client.get(
            f"/api/v1/composer/drafts/{step.draft_id}/model-steps/{step.id}"
        )
        cancelled = client.delete(
            f"/api/v1/composer/drafts/{step.draft_id}/model-steps/{step.id}",
            headers={"X-CSRF-Token": "csrf"},
        )

    assert status.status_code == 200
    assert cancelled.status_code == 200
    repository.get_model_step.assert_called_once_with(
        step.owner_id, step.draft_id, step.id
    )
    repository.cancel_model_step.assert_called_once_with(
        step.owner_id, step.draft_id, step.id
    )


@pytest.mark.unit
def test_invalid_model_step_input_and_missing_preconditions_do_not_start(
    mocker: MockerFixture,
) -> None:
    client, runner, _repository = _client(mocker)
    step = _step()
    sensitive = "private-" + "document-excerpt"
    body = {
        "connection_id": str(step.connection_id),
        "approved_endpoint": step.approved_endpoint,
        "approved_model": step.model,
        "content": sensitive,
        "max_output_tokens": 8,
    }
    with client:
        missing = client.post(
            f"/api/v1/composer/drafts/{step.draft_id}/model-steps",
            json=body,
            headers={"X-CSRF-Token": "csrf"},
        )
        invalid = client.post(
            f"/api/v1/composer/drafts/{step.draft_id}/model-steps",
            json={**body, "connection_id": "invalid"},
            headers={
                "If-Match": '"3"',
                "Idempotency-Key": "model-step-1",
                "X-CSRF-Token": "csrf",
            },
        )
        oversized = client.post(
            f"/api/v1/composer/drafts/{step.draft_id}/model-steps",
            json={**body, "content": "x" * 2048},
            headers={
                "If-Match": '"3"',
                "Idempotency-Key": "model-step-1",
                "X-CSRF-Token": "csrf",
            },
        )

    assert missing.status_code == 428
    assert invalid.status_code == 422
    assert sensitive not in invalid.text
    assert oversized.status_code == 422
    runner.start.assert_not_called()


@pytest.mark.unit
def test_global_model_step_capacity_returns_configured_retry_without_content(
    mocker: MockerFixture,
) -> None:
    client, runner, _repository = _client(mocker)
    step = _step()
    runner.start.side_effect = ComposerCapacityError("Model call capacity is busy")
    private_text = "private-" + "document-excerpt"

    with client:
        response = client.post(
            f"/api/v1/composer/drafts/{step.draft_id}/model-steps",
            json={
                "connection_id": str(step.connection_id),
                "approved_endpoint": step.approved_endpoint,
                "approved_model": step.model,
                "content": private_text,
                "max_output_tokens": 8,
            },
            headers={
                "If-Match": '"3"',
                "Idempotency-Key": "model-step-capacity",
                "X-CSRF-Token": "csrf",
            },
        )
        documented = client.app.openapi()["paths"][
            "/api/v1/composer/drafts/{draft_id}/model-steps"
        ]["post"]["responses"]["503"]["headers"]

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "7"
    assert response.json()["error"]["code"] == "COMPOSER_CAPACITY_EXHAUSTED"
    assert private_text not in response.text
    assert "Retry-After" in documented


@pytest.mark.unit
def test_question_reads_and_answer_work_without_model_runner(
    mocker: MockerFixture,
) -> None:
    client, _runner, repository = _client(mocker, enabled=False)
    step = _step()
    now = datetime.now(UTC)
    question = ComposerQuestion(
        id=UUID(int=99),
        draft_id=step.draft_id,
        model_step_id=step.id,
        base_version=3,
        state="pending",
        text="What is the missing date?",
        answer_message_id=None,
        answer_content=None,
        created_at=now,
        answered_at=None,
    )
    repository.list_questions.return_value = (question,)
    repository.get_question.return_value = question
    repository.answer_question.return_value = (question, '"5"')
    base = f"/api/v1/composer/drafts/{step.draft_id}/questions"
    with client:
        listed = client.get(f"{base}?order=desc&limit=1&offset=2")
        invalid_order = client.get(f"{base}?order=random")
        fetched = client.get(f"{base}/{question.id}")
        missing = client.post(
            f"{base}/{question.id}/answer",
            json={"content": "Tomorrow"},
            headers={"X-CSRF-Token": "csrf"},
        )
        answered = client.post(
            f"{base}/{question.id}/answer",
            json={"content": "Tomorrow"},
            headers={
                "If-Match": '"4"',
                "Idempotency-Key": "answer-1",
                "X-CSRF-Token": "csrf",
            },
        )
    assert listed.status_code == fetched.status_code == answered.status_code == 200
    assert invalid_order.status_code == 422
    assert missing.status_code == 428
    assert listed.json()["questions"][0]["id"] == str(question.id)
    repository.list_questions.assert_called_once_with(
        step.owner_id, step.draft_id, limit=1, offset=2, order="desc"
    )
    assert fetched.json()["text"] == question.text
    assert answered.headers["ETag"] == '"5"'
    repository.answer_question.assert_called_once_with(
        step.owner_id,
        step.draft_id,
        question.id,
        content="Tomorrow",
        if_match='"4"',
        idempotency_key="answer-1",
    )


@pytest.mark.unit
def test_question_step_intent_and_answer_link_are_passed_to_runner(
    mocker: MockerFixture,
) -> None:
    client, runner, _repository = _client(mocker)
    step = _step()
    runner.start.return_value = step
    with client:
        response = client.post(
            f"/api/v1/composer/drafts/{step.draft_id}/model-steps",
            json={
                "connection_id": str(step.connection_id),
                "approved_endpoint": step.approved_endpoint,
                "approved_model": step.model,
                "content": "What should be clarified?",
                "max_output_tokens": 8,
                "intent": "question",
                "answered_question_id": str(UUID(int=99)),
            },
            headers={
                "If-Match": '"3"',
                "Idempotency-Key": "model-step-question",
                "X-CSRF-Token": "csrf",
            },
        )
    assert response.status_code == 202
    assert runner.start.call_args.args[2].intent == "question"
    assert runner.start.call_args.args[2].answered_question_id == UUID(int=99)
