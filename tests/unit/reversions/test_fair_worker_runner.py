"""Deterministic scheduling coverage for mixed conversion workers."""

from __future__ import annotations

from typing import Any, cast

import pytest
from pytest_mock import MockerFixture

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.jobs.runner import WorkerSchedule
from markweave.jobs.worker import ConversionWorker
from markweave.observability import OperationalMetrics, QueueSnapshot
from markweave.persistence.errors import PersistenceError
from markweave.reversion_jobs.errors import (
    ReversionJobLeaseLostError,
    ReversionProofRequiredError,
)
from markweave.reversion_jobs.runner import (
    FairWorkerLoop,
    ReversionSchedule,
    StopSignalBridge,
)
from markweave.reversion_jobs.worker import ReversionWorker
from markweave.reversion_jobs.worker_maintenance import ReversionRecoveryResult
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.storage import ObjectStoreError

pytestmark = pytest.mark.unit


class _StopAfter:
    def __init__(self, events: list[str], count: int) -> None:
        self.events = events
        self.count = count
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return len(self.events) >= self.count

    def wait(self, timeout: float) -> bool:
        self.waits.append(timeout)
        return True


class _StopAfterChecks(_StopAfter):
    def __init__(self, checks: int) -> None:
        super().__init__([], 1)
        self._remaining = checks

    def is_set(self) -> bool:
        self._remaining -= 1
        return self._remaining < 0


def _loop(
    mocker: MockerFixture,
    events: list[str],
    *,
    forward_schedule: WorkerSchedule | None = None,
    reverse_schedule: ReversionSchedule | None = None,
    metrics: OperationalMetrics | None = None,
) -> tuple[FairWorkerLoop, Any, Any, StopSignalBridge]:
    forward = mocker.Mock(spec=ConversionWorker)
    reverse = mocker.Mock(spec=ReversionWorker)
    forward.recover.return_value = 0
    reverse.recover.return_value = 0
    forward.cleanup.return_value = 0
    reverse.cleanup.return_value = 0
    reverse.reconcile_step.return_value = True
    reverse.recover_step.return_value = 0
    forward.run_once.side_effect = lambda **_kwargs: events.append("forward") or True
    reverse.run_reconciled_once.side_effect = lambda: events.append("reverse") or True
    bridge = StopSignalBridge()
    clock_value = 0.0

    def clock() -> float:
        nonlocal clock_value
        clock_value += 0.01
        return clock_value

    loop = FairWorkerLoop(
        cast(ConversionWorker, forward),
        cast(ReversionWorker, reverse),
        forward_schedule or WorkerSchedule(0.1, 100, 5, 2),
        reverse_schedule or ReversionSchedule(100, 3),
        stop_bridge=bridge,
        monotonic_clock=clock,
        metrics=metrics,
    )
    return loop, forward, reverse, bridge


def test_forward_starts_and_reverse_gets_a_bounded_scheduling_turn(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    loop, _forward, _reverse, bridge = _loop(mocker, events)
    stop = _StopAfter(events, 5)

    loop.run(stop)

    assert events == ["forward", "forward", "forward", "reverse", "forward"]
    assert bridge.is_set()


def test_empty_preferred_family_falls_back_immediately(mocker: MockerFixture) -> None:
    events: list[str] = []
    loop, forward, reverse, _bridge = _loop(mocker, events)
    forward.run_once.side_effect = lambda **_kwargs: (
        events.append("forward-empty") or False
    )
    reverse.reconcile_step.return_value = False

    loop.run(_StopAfter(events, 2))

    assert events == ["forward-empty", "forward-empty"]


def test_reverse_broker_failure_does_not_block_forward(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    log_event = mocker.patch("markweave.observability.log_event")
    metrics = OperationalMetrics()
    loop, forward, reverse, _bridge = _loop(mocker, events, metrics=metrics)
    forward.run_once.side_effect = [False, True]
    reverse.reconcile_step.side_effect = BrokerError(
        BrokerErrorCategory.TRANSPORT_FAILURE
    )
    stop = _StopAfterChecks(2)
    loop.run(stop)

    assert forward.run_once.call_count >= 1
    reverse.reconcile_step.assert_called_once_with()
    rendered = metrics.render(QueueSnapshot(0, 0, 0))
    assert (
        'md_converter_reversion_runtime_faults_total{code="transport_failure"} 1'
        in rendered
    )
    log_event.assert_any_call(
        "reversion_runtime_fault_observed", error_code="transport_failure"
    )
    assert all(
        call.args[0] != "reversion_job_processing_failed"
        for call in log_event.call_args_list
    )


def test_reverse_recovery_failure_does_not_block_forward(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    loop, forward, reverse, _bridge = _loop(mocker, events)
    reverse.recover_step.side_effect = BrokerError(
        BrokerErrorCategory.TRANSPORT_FAILURE
    )

    loop.run(_StopAfter(events, 3))

    assert forward.run_once.call_count == 3
    reverse.recover_step.assert_called_once_with()
    reverse.run_reconciled_once.assert_not_called()


def test_reverse_semantic_protocol_failure_does_not_block_forward(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    loop, forward, reverse, _bridge = _loop(mocker, events)
    reverse.reconcile_step.side_effect = ReverseConversionError(
        ReverseErrorCategory.PROTOCOL_ERROR
    )

    loop.run(_StopAfter(events, 2))

    assert forward.run_once.call_count == 2
    reverse.reconcile_step.assert_called_once_with()


@pytest.mark.parametrize(
    ("error", "code", "fault_gauge"),
    [
        (
            ReverseConversionError(ReverseErrorCategory.PROTOCOL_ERROR),
            "protocol_error",
            "md_converter_reversion_runtime_fault 1",
        ),
        (
            ReversionJobLeaseLostError("lost"),
            "lease_lost",
            "md_converter_reversion_reconciliation_fault 1",
        ),
        (
            ObjectStoreError("failed"),
            "object_store_failure",
            "md_converter_reversion_runtime_fault 1",
        ),
        (
            PersistenceError(),
            "persistence_failure",
            "md_converter_reversion_reconciliation_fault 1",
        ),
    ],
)
def test_reverse_fault_categories_publish_their_exact_degraded_scope(
    error: Exception,
    code: str,
    fault_gauge: str,
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    metrics = OperationalMetrics()
    loop, forward, reverse, _bridge = _loop(mocker, events, metrics=metrics)
    forward.run_once.side_effect = [False, True]
    reverse.reconcile_step.side_effect = error

    loop.run(_StopAfterChecks(2))

    rendered = metrics.render(QueueSnapshot(0, 0, 0))
    assert fault_gauge in rendered
    assert f'md_converter_reversion_runtime_faults_total{{code="{code}"}} 1' in rendered


def test_multi_page_reconciliation_interleaves_forward_before_reverse_claim(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    loop, forward, reverse, _bridge = _loop(mocker, events)
    forward.run_once.side_effect = lambda **_kwargs: events.append("forward") or True
    reverse.reconcile_step.side_effect = lambda: (
        events.append("reconcile") or reverse.reconcile_step.call_count >= 3
    )
    reverse.recover_step.side_effect = lambda: events.append("recover") or 0
    reverse.run_reconciled_once.side_effect = lambda: events.append("reverse") or True

    loop.run(_StopAfter(events, 10))

    assert events == [
        "forward",
        "reconcile",
        "forward",
        "reconcile",
        "forward",
        "reconcile",
        "forward",
        "recover",
        "forward",
        "reverse",
    ]


def test_proof_recovery_returns_to_reconciliation_without_claiming(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    metrics = OperationalMetrics()
    loop, forward, reverse, _bridge = _loop(mocker, events, metrics=metrics)
    forward.run_once.side_effect = lambda **_kwargs: events.append("forward") or True

    def reconcile() -> bool:
        events.append("reconcile")
        if reverse.reconcile_step.call_count == 1:
            raise ReversionProofRequiredError
        return False

    reverse.reconcile_step.side_effect = reconcile
    reverse.recover_step.side_effect = lambda: (
        events.append("recover") or ReversionRecoveryResult(1, 0)
    )

    loop.run(_StopAfter(events, 7))

    assert events == [
        "forward",
        "reconcile",
        "forward",
        "recover",
        "forward",
        "reconcile",
        "forward",
    ]
    reverse.run_reconciled_once.assert_not_called()
    rendered = metrics.render(QueueSnapshot(0, 0, 0))
    assert "md_converter_reversion_reconciliation_ready 0" in rendered
    assert "md_converter_reversion_degraded 1" in rendered
    assert "md_converter_reversion_recoveries_total 0" in rendered


def test_due_reverse_cleanup_is_one_quantum_when_both_queues_are_empty(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    metrics = OperationalMetrics()
    loop, forward, reverse, _bridge = _loop(
        mocker,
        events,
        reverse_schedule=ReversionSchedule(0.01, 3),
        metrics=metrics,
    )
    forward.run_once.side_effect = None
    forward.run_once.return_value = False
    reverse.cleanup.return_value = 2

    loop.run(_StopAfterChecks(1))

    reverse.cleanup.assert_called_once_with()
    reverse.reconcile_step.assert_not_called()
    rendered = metrics.render(QueueSnapshot(0, 0, 0))
    assert "md_converter_reversion_expirations_total 2" in rendered
    assert (
        'md_converter_reversion_operation_duration_seconds_count{operation="reversion_cleanup"} 1'
        in rendered
    )


def test_reverse_cleanup_failure_isolated_and_scheduled_for_retry(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    metrics = OperationalMetrics()
    loop, forward, reverse, _bridge = _loop(
        mocker,
        events,
        reverse_schedule=ReversionSchedule(0.01, 3),
        metrics=metrics,
    )
    forward.run_once.side_effect = None
    forward.run_once.return_value = False
    reverse.cleanup.side_effect = ObjectStoreError("failed")

    loop.run(_StopAfterChecks(1))

    reverse.cleanup.assert_called_once_with()
    rendered = metrics.render(QueueSnapshot(0, 0, 0))
    assert "md_converter_reversion_runtime_fault 1" in rendered
    assert (
        'md_converter_reversion_worker_retries_total{operation="reversion_cleanup"} 1'
        in rendered
    )


def test_forward_recovery_and_cleanup_failures_remain_retryable(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    metrics = OperationalMetrics()
    loop, forward, _reverse, _bridge = _loop(
        mocker,
        events,
        forward_schedule=WorkerSchedule(0.1, 0.01, 5, 2),
        metrics=metrics,
    )
    forward.recover.side_effect = PersistenceError()
    forward.cleanup.side_effect = PersistenceError()

    loop.run(_StopAfterChecks(1))

    forward.run_once.assert_not_called()
    forward.cleanup.assert_called_once_with(limit=5)
    rendered = metrics.render(QueueSnapshot(0, 0, 0))
    assert 'md_converter_worker_retries_total{operation="worker_loop"} 2' in rendered


def test_due_forward_cleanup_success_does_not_schedule_a_retry(
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    metrics = OperationalMetrics()
    loop, forward, _reverse, _bridge = _loop(
        mocker,
        events,
        forward_schedule=WorkerSchedule(0.1, 0.01, 5, 2),
        metrics=metrics,
    )
    forward.run_once.side_effect = None
    forward.run_once.return_value = False

    loop.run(_StopAfterChecks(1))

    forward.cleanup.assert_called_once_with(limit=5)
    assert "md_converter_worker_retries_total" not in metrics.render(
        QueueSnapshot(0, 0, 0)
    )


def test_stop_bridge_is_inert_until_bound_and_rejects_rebinding() -> None:
    bridge = StopSignalBridge()
    first = _StopAfter([], 1)
    second = _StopAfter([], 1)

    assert not bridge.is_set()
    assert not bridge.wait(0.01)
    bridge.bind(first)
    bridge.bind(first)
    with pytest.raises(RuntimeError, match="already bound"):
        bridge.bind(second)


@pytest.mark.parametrize("values", [(0, 1), (1, 0), (float("inf"), 1), (True, 1)])
def test_reverse_schedule_requires_positive_finite_values(
    values: tuple[float, float],
) -> None:
    with pytest.raises(ValueError, match="positive"):
        ReversionSchedule(*values)
