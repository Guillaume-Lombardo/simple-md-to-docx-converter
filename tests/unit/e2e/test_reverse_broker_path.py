"""Bound the worker-to-broker path readiness gate of the reverse lifecycle."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pytest
from pytest_mock import MockerFixture, MockType

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.jobs.runner import WorkerSchedule
from markweave.jobs.worker import ConversionWorker
from markweave.observability import OperationalMetrics, QueueSnapshot
from markweave.reversion_jobs.runner import (
    FairWorkerLoop,
    ReversionSchedule,
    StopSignalBridge,
)
from markweave.reversion_jobs.worker import ReversionWorker
from scripts.e2e import reverse_broker as fixture

pytestmark = pytest.mark.unit

METRICS_URL = "http://127.0.0.1:9464/metrics"
ATTEMPTS = (
    "md_converter_reversion_operation_duration_seconds_count"
    '{operation="reversion_reconciliation"}'
)
SUCCESSES = "md_converter_reversion_reconciliation_successes_total"
TRANSPORT = 'md_converter_reversion_runtime_faults_total{code="transport_failure"}'
UNPROVEN = 'md_converter_reversion_runtime_faults_total{code="termination_unproven"}'


def _sample(  # noqa: PLR0913 - one explicit metrics scrape
    successes: float,
    *,
    attempts: float | None = None,
    transport: float = 0,
    unproven: float = 0,
    ready: float = 1,
    fault: float = 0,
) -> dict[str, float]:
    return {
        "md_converter_reversion_broker_ready": ready,
        "md_converter_reversion_broker_fault": fault,
        ATTEMPTS: successes if attempts is None else attempts,
        SUCCESSES: successes,
        TRANSPORT: transport,
        UNPROVEN: unproven,
        "md_converter_reversion_queue_depth": 0,
    }


def _script(
    mocker: MockerFixture,
    samples: Iterable[dict[str, float]],
    *,
    clock: Iterable[float] | None = None,
) -> tuple[MockType, MockType]:
    reader = mocker.patch.object(fixture, "_read_metrics", side_effect=list(samples))
    sleep = mocker.patch.object(fixture.time, "sleep")
    if clock is not None:
        mocker.patch.object(fixture.time, "monotonic", side_effect=list(clock))
    return reader, sleep


def test_consecutive_successful_reconciliations_prove_the_path(
    mocker: MockerFixture,
) -> None:
    reader, sleep = _script(
        mocker,
        [_sample(10), _sample(11), _sample(13)],
        clock=[0.0, 0.25, 0.5, 0.75],
    )

    assert fixture.wait_worker_broker(METRICS_URL) == {
        "elapsed_seconds": 0.75,
        "faults_observed": 0,
    }
    assert reader.call_count == 3
    sleep.assert_called_with(0.25)


def test_a_new_fault_restarts_the_success_count(
    mocker: MockerFixture,
) -> None:
    samples = [
        _sample(10, attempts=12, ready=0, fault=1, transport=4),
        _sample(10, attempts=13, ready=0, fault=1, transport=5),
        _sample(10, attempts=14, ready=0, fault=1, transport=6),
        _sample(11, attempts=15, transport=6),
        _sample(12, attempts=16, transport=6),
        _sample(13, attempts=17, transport=6),
    ]
    reader, _ = _script(mocker, samples)

    evidence = fixture.wait_worker_broker(METRICS_URL)

    assert evidence["faults_observed"] == 2
    assert reader.call_count == 6


def test_any_fault_code_restarts_the_count(mocker: MockerFixture) -> None:
    samples = [
        _sample(10),
        _sample(12, attempts=13, unproven=1),
        _sample(14, attempts=15, unproven=1),
        _sample(15, attempts=16, unproven=1),
    ]
    reader, _ = _script(mocker, samples)

    assert fixture.wait_worker_broker(METRICS_URL)["faults_observed"] == 1
    assert reader.call_count == 4


def test_a_failed_step_restarts_the_count_before_its_fault_is_published(
    mocker: MockerFixture,
) -> None:
    samples = [
        _sample(10),
        _sample(12),
        _sample(14, attempts=15),
        _sample(14, attempts=15, ready=0, fault=1, transport=1),
        _sample(16, attempts=17, transport=1),
        _sample(17, attempts=18, transport=1),
    ]
    reader, _ = _script(mocker, samples)

    assert fixture.wait_worker_broker(METRICS_URL)["faults_observed"] == 1
    assert reader.call_count == 6


def test_reconciliation_attempts_without_successes_never_prove_the_path(
    mocker: MockerFixture,
) -> None:
    _script(
        mocker,
        [_sample(5, attempts=5), _sample(5, attempts=9), _sample(5, attempts=12)],
        clock=[0.0, 0.0, 5.0, 10.0],
    )

    with pytest.raises(RuntimeError, match="readiness timed out"):
        fixture.wait_worker_broker(METRICS_URL, timeout_seconds=10.0)


def test_a_counter_reset_starts_a_new_baseline(mocker: MockerFixture) -> None:
    samples = [
        _sample(40, attempts=41, transport=5),
        _sample(41, attempts=42, transport=5),
        _sample(1, transport=1),
        _sample(3, transport=1),
        _sample(4, transport=1),
    ]
    reader, _ = _script(mocker, samples)

    assert fixture.wait_worker_broker(METRICS_URL)["faults_observed"] == 1
    assert reader.call_count == 5


def test_a_success_counter_reset_alone_starts_a_new_baseline(
    mocker: MockerFixture,
) -> None:
    samples = [_sample(41), _sample(2), _sample(4), _sample(5)]
    reader, _ = _script(mocker, samples)

    assert fixture.wait_worker_broker(METRICS_URL)["faults_observed"] == 0
    assert reader.call_count == 4


def test_degraded_gauges_hold_the_gate_without_a_new_fault(
    mocker: MockerFixture,
) -> None:
    samples = [
        _sample(10, ready=0, fault=1),
        _sample(14, ready=0, fault=1),
        _sample(15),
    ]
    reader, _ = _script(mocker, samples)

    assert fixture.wait_worker_broker(METRICS_URL)["faults_observed"] == 0
    assert reader.call_count == 3


def test_a_stalled_path_times_out_with_the_last_metrics(
    mocker: MockerFixture,
) -> None:
    _script(
        mocker,
        [
            _sample(10, attempts=11, ready=0, fault=1, transport=1),
            _sample(10, attempts=12, ready=0, fault=1, transport=2),
            _sample(10, attempts=13, ready=0, fault=1, transport=3),
        ],
        clock=[0.0, 0.0, 5.0, 10.0],
    )

    with pytest.raises(RuntimeError, match="readiness timed out") as raised:
        fixture.wait_worker_broker(METRICS_URL, timeout_seconds=10.0)

    assert '"faults": 3' in str(raised.value)
    assert '"successes": 10' in str(raised.value)
    assert '"attempts": 13' in str(raised.value)


def test_missing_counters_default_to_zero(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(
        fixture,
        "_read_metrics",
        return_value={
            "md_converter_reversion_broker_ready": 1.0,
            "md_converter_reversion_broker_fault": 0.0,
        },
    )

    assert fixture._broker_path_metrics(METRICS_URL) == {
        "broker_ready": 1.0,
        "broker_fault": 0.0,
        "attempts": 0.0,
        "successes": 0.0,
        "faults": 0,
    }


def test_missing_runtime_gauges_fail_closed(mocker: MockerFixture) -> None:
    mocker.patch.object(fixture, "_read_metrics", return_value={SUCCESSES: 3.0})

    with pytest.raises(RuntimeError, match="gauges are unavailable"):
        fixture.wait_worker_broker(METRICS_URL)


class _StopAfterReconciliations:
    def __init__(self, reverse: MockType, count: int) -> None:
        self._reverse = reverse
        self._count = count

    def is_set(self) -> bool:
        return self._reverse.reconcile_step.call_count >= self._count

    def wait(self, timeout: float) -> bool:
        del timeout
        return False


def test_a_scrape_inside_a_failed_real_worker_step_keeps_the_gate_closed(
    mocker: MockerFixture,
) -> None:
    """Scrape the real worker metrics between failed-step duration and fault."""
    metrics = OperationalMetrics()
    scrapes = [metrics.render(QueueSnapshot(0, 0, 0))]
    forward = mocker.Mock(spec=ConversionWorker)
    forward.run_once.return_value = False
    reverse = mocker.Mock(spec=ReversionWorker)
    reverse.reconcile_step.side_effect = [
        False,
        False,
        BrokerError(BrokerErrorCategory.TRANSPORT_FAILURE),
        False,
        False,
        False,
    ]
    publish_duration = metrics.record_reversion_duration
    publish_retry = metrics.record_reversion_retry

    def scrape_after_duration(operation: str, seconds: float) -> None:
        publish_duration(operation, seconds)
        scrapes.append(metrics.render(QueueSnapshot(0, 0, 0)))

    def scrape_after_fault_handling(operation: str) -> None:
        publish_retry(operation)
        scrapes.append(metrics.render(QueueSnapshot(0, 0, 0)))

    mocker.patch.object(
        metrics, "record_reversion_duration", side_effect=scrape_after_duration
    )
    mocker.patch.object(
        metrics, "record_reversion_retry", side_effect=scrape_after_fault_handling
    )
    clock_value = 0.0

    def clock() -> float:
        nonlocal clock_value
        clock_value += 0.01
        return clock_value

    loop = FairWorkerLoop(
        cast(ConversionWorker, forward),
        cast(ReversionWorker, reverse),
        WorkerSchedule(0.1, 100, 5, 2),
        ReversionSchedule(100, 0.01),
        stop_bridge=StopSignalBridge(),
        monotonic_clock=clock,
        metrics=metrics,
    )
    loop.run(_StopAfterReconciliations(reverse, 6))

    in_failed_step = scrapes[3]
    assert f"{ATTEMPTS} 3" in in_failed_step
    assert f"{SUCCESSES} 2" in in_failed_step
    assert "md_converter_reversion_broker_ready 1" in in_failed_step
    assert "md_converter_reversion_broker_fault 0" in in_failed_step
    assert TRANSPORT not in in_failed_step
    assert f"{TRANSPORT} 1" in scrapes[4]
    assert len(scrapes) == 8
    responses = []
    for scrape in scrapes:
        response = mocker.MagicMock()
        response.__enter__.return_value.read.return_value = scrape.encode()
        responses.append(response)
    urlopen = mocker.patch.object(
        fixture.urllib.request, "urlopen", side_effect=responses
    )
    mocker.patch.object(fixture.time, "sleep")

    evidence = fixture.wait_worker_broker(METRICS_URL)

    assert urlopen.call_count == len(scrapes)
    assert evidence["faults_observed"] == 1


def test_cli_requires_a_metrics_url(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    gate = mocker.patch.object(fixture, "wait_worker_broker")
    mocker.patch.object(
        sys, "argv", ["reverse_broker", "wait-worker-broker", "--root", str(tmp_path)]
    )

    with pytest.raises(SystemExit):
        fixture.main()

    gate.assert_not_called()
    assert "requires metrics URL" in capsys.readouterr().err


def test_cli_prints_the_gate_evidence(
    mocker: MockerFixture, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    gate = mocker.patch.object(
        fixture,
        "wait_worker_broker",
        return_value={"elapsed_seconds": 1.5, "faults_observed": 2.0},
    )
    mocker.patch.object(
        sys,
        "argv",
        [
            "reverse_broker",
            "wait-worker-broker",
            "--root",
            str(tmp_path),
            "--metrics-url",
            METRICS_URL,
        ],
    )

    fixture.main()

    gate.assert_called_once_with(METRICS_URL)
    assert capsys.readouterr().out == (
        "Reverse worker broker path ready: "
        '{"elapsed_seconds": 1.5, "faults_observed": 2.0}\n'
    )
