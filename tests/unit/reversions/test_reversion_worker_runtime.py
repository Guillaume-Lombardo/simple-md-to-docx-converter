"""Unit coverage for injected reverse-worker runtime policy."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture

from markweave.broker.errors import BrokerError
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    RuntimeChannelLimits,
    RuntimeLimits,
)
from markweave.broker.protocol import ReadyRequest, ReadyResponse
from markweave.reversion_jobs.runtime import (
    ReversionWorkerPolicy,
    ReversionWorkerRuntime,
)
from markweave.reversion_jobs.worker import ReversionWorker
from markweave.reversions.models import ReverseContentLimits

pytestmark = pytest.mark.unit

POLICY = ReversionWorkerPolicy(30, 5, 120, 3600, 300, 1, 30, 16, 30, 16)
CONTENT_LIMITS = ReverseContentLimits(
    10_000,
    30_000,
    10_000,
    100,
    100,
    10_000,
    100,
    16,
    8,
    20_000,
    20_000,
    10_000,
    30_000,
)
BROKER_POLICY = BrokerPolicy(
    "t71-test",
    "sha256:" + "a" * 64,
    RuntimeLimits(100_000, 100_000, 512_000_000, 32, 16_000_000, 30_000),
    RuntimeChannelLimits(10_000, 30_000),
)


def test_worker_policy_accepts_only_coherent_injected_values() -> None:
    assert POLICY.collect_poll_seconds == 1

    for changes in (
        {"lease_seconds": 0},
        {"lease_seconds": float("inf")},
        {"lease_seconds": float("nan")},
        {"recovery_batch_size": 0},
        {"heartbeat_seconds": 30},
        {"collect_poll_seconds": 6},
    ):
        with pytest.raises(ValueError):
            replace(POLICY, **changes)


def _runtime(mocker: MockerFixture, **changes: Any) -> ReversionWorkerRuntime:
    values: dict[str, Any] = {
        "repository": mocker.Mock(),
        "objects": mocker.Mock(),
        "broker": mocker.Mock(),
        "reconciler": mocker.Mock(),
        "principal": AuthenticatedPrincipal(
            UUID("10000000-0000-4000-8000-000000000001")
        ),
        "broker_policy": BROKER_POLICY,
        "content_limits": CONTENT_LIMITS,
        "policy": POLICY,
        "worker_id": "reverse-worker",
        "clock": mocker.Mock(),
        "monotonic_clock": mocker.Mock(),
        "wait": mocker.Mock(),
        "shutdown_requested": mocker.Mock(),
        "request_id_factory": uuid4,
    }
    values.update(changes)
    return ReversionWorkerRuntime(**values)


def test_runtime_keeps_all_product_values_and_boundaries_injected(
    mocker: MockerFixture,
) -> None:
    runtime = _runtime(mocker)

    assert runtime.policy is POLICY
    assert runtime.content_limits is CONTENT_LIMITS
    assert runtime.broker_policy is BROKER_POLICY
    assert runtime.worker_id == "reverse-worker"


def test_production_worker_requires_bound_ready_response_after_reconciliation(
    mocker: MockerFixture,
) -> None:
    runtime = _runtime(
        mocker,
        require_ready=True,
        shutdown_requested=lambda: False,
        clock=lambda: datetime(2026, 9, 6, tzinfo=UTC),
    )
    broker = cast(Any, runtime.broker)
    repository = cast(Any, runtime.repository)
    events: list[str] = []
    cast(Any, runtime.reconciler).reconcile.side_effect = lambda *_args, **_kwargs: (
        events.append("reconcile")
    )
    broker.request.side_effect = lambda request: (
        events.append("ready") or ReadyResponse(request.request_id, True)
    )
    repository.claim.return_value = None

    assert not ReversionWorker(runtime).run_once()
    assert events == ["reconcile", "ready"]
    assert isinstance(broker.request.call_args.args[0], ReadyRequest)
    cast(Any, runtime.reconciler).reconcile.assert_called_once()

    broker.request.side_effect = lambda request: ReadyResponse(uuid4(), True)
    with pytest.raises(BrokerError):
        ReversionWorker(runtime).run_once()


@pytest.mark.parametrize(
    "response",
    (
        object(),
        ReadyResponse(UUID("10000000-0000-4000-8000-000000000002"), True),
        None,
    ),
)
def test_completed_reconciliation_never_claims_after_invalid_ready_response(
    mocker: MockerFixture, response: object
) -> None:
    runtime = _runtime(
        mocker,
        require_ready=True,
        shutdown_requested=lambda: False,
        clock=lambda: datetime(2026, 9, 6, tzinfo=UTC),
    )
    broker = cast(Any, runtime.broker)
    repository = cast(Any, runtime.repository)
    broker.request.side_effect = (
        (lambda request: ReadyResponse(request.request_id, False))
        if response is None
        else (lambda _request: response)
    )

    with pytest.raises(BrokerError):
        ReversionWorker(runtime).run_once()

    cast(Any, runtime.reconciler).reconcile.assert_called_once()
    repository.claim.assert_not_called()


def test_reconciliation_step_requires_ready_only_after_the_fixed_point(
    mocker: MockerFixture,
) -> None:
    runtime = _runtime(
        mocker,
        require_ready=True,
        clock=lambda: datetime(2026, 9, 6, tzinfo=UTC),
    )
    broker = cast(Any, runtime.broker)
    reconciler = cast(Any, runtime.reconciler)
    reconciler.reconcile_step.side_effect = (False, True)
    broker.request.side_effect = lambda request: ReadyResponse(request.request_id, True)
    worker = ReversionWorker(runtime)

    assert not worker.reconcile_step()
    broker.request.assert_not_called()
    assert worker.reconcile_step()
    assert isinstance(broker.request.call_args.args[0], ReadyRequest)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("principal", object()),
        ("broker_policy", object()),
        ("content_limits", object()),
        ("policy", object()),
        ("worker_id", " "),
        ("worker_id", "x" * 256),
        ("clock", None),
        ("request_id_factory", None),
    ],
)
def test_runtime_rejects_invalid_identity_or_callable(
    mocker: MockerFixture, field: str, value: object
) -> None:
    with pytest.raises(ValueError):
        _runtime(mocker, **{field: cast(Any, value)})
