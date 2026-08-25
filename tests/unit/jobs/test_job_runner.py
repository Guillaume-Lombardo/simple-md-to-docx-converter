"""Unit coverage for embedded and external worker loop lifecycle."""

from __future__ import annotations

from threading import Event

import pytest
from pytest_mock import MockerFixture

from md_converter.jobs.errors import JobRepositoryError
from md_converter.jobs.runner import (
    EmbeddedWorker,
    ExternalWorkerRuntime,
    WorkerLoop,
    WorkerSchedule,
)
from md_converter.jobs.worker import ConversionWorker
from md_converter.observability import (
    MetricsHttpServer,
    OperationalMetrics,
    QueueSnapshot,
)
from md_converter.persistence.errors import PersistenceError

pytestmark = pytest.mark.unit


def test_external_loop_recovers_processes_cleans_and_waits(
    mocker: MockerFixture,
) -> None:
    worker = mocker.Mock(spec=ConversionWorker)
    worker.run_once.side_effect = (True, False, False)
    stop = mocker.Mock()
    stop.is_set.side_effect = (False, False, False, True)
    stop.wait.side_effect = (False, True)
    clock = mocker.Mock(side_effect=(0.0, 1.0, 2.0, 2.5))
    WorkerLoop(
        worker,
        WorkerSchedule(0.1, 2, 5, 0.2),
        monotonic_clock=clock,
    ).run(stop)
    assert worker.recover.call_count == 3
    assert worker.run_once.call_count == 3
    shutdown_probe = worker.run_once.call_args_list[0].kwargs["shutdown_requested"]
    assert callable(shutdown_probe)
    worker.cleanup.assert_called_once_with(limit=5)
    assert stop.wait.call_count == 2


def test_embedded_worker_starts_once_and_stops_cleanly(
    mocker: MockerFixture,
) -> None:
    entered = Event()

    loop = mocker.Mock(spec=WorkerLoop)

    def run(stop: Event) -> None:
        entered.set()
        stop.wait(1)

    loop.run.side_effect = run
    embedded = EmbeddedWorker(loop, thread_name="embedded-test")
    embedded.start()
    assert entered.wait(1)
    with pytest.raises(RuntimeError, match="already running"):
        embedded.start()
    embedded.stop(timeout_seconds=1)
    embedded.stop(timeout_seconds=1)
    with pytest.raises(ValueError):
        embedded.stop(timeout_seconds=0)

    with pytest.raises(ValueError, match="thread name"):
        EmbeddedWorker(loop, thread_name="")

    stuck = EmbeddedWorker(loop, thread_name="stuck")
    stuck_thread = mocker.Mock()
    stuck_thread.is_alive.return_value = True
    stuck._thread = stuck_thread
    with pytest.raises(RuntimeError, match="did not stop"):
        stuck.stop(timeout_seconds=1)


def test_loop_retries_transient_failure_and_embedded_worker_exposes_fatal_error(
    mocker: MockerFixture,
) -> None:
    worker = mocker.Mock(spec=ConversionWorker)
    worker.recover.side_effect = (JobRepositoryError(), None)
    worker.run_once.return_value = False
    stop = mocker.Mock()
    stop.is_set.side_effect = (False, False, True)
    metrics = OperationalMetrics()
    WorkerLoop(worker, WorkerSchedule(0.1, 2, 5, 0.2), metrics=metrics).run(stop)
    worker.run_once.assert_called_once()
    assert [call.args[0] for call in stop.wait.call_args_list] == [0.2, 0.1]
    assert (
        'md_converter_worker_retries_total{operation="worker_loop"} 1'
        in metrics.render(QueueSnapshot(0, 0, 0))
    )

    loop = mocker.Mock(spec=WorkerLoop)
    failure = RuntimeError("fatal loop failure")
    loop.run.side_effect = failure
    embedded = EmbeddedWorker(loop, thread_name="fatal-worker")
    embedded.start()
    assert embedded._thread is not None
    embedded._thread.join(1)
    assert embedded.failure is failure
    embedded.stop(timeout_seconds=1)


def test_cleanup_failure_advances_cadence_before_transient_backoff(
    mocker: MockerFixture,
) -> None:
    worker = mocker.Mock(spec=ConversionWorker)
    worker.run_once.return_value = False
    worker.cleanup.side_effect = (PersistenceError(), None)
    stop = mocker.Mock()
    stop.is_set.side_effect = (False, False, False, True)
    clock = mocker.Mock(side_effect=(0.0, 2.0, 2.5, 4.0))

    WorkerLoop(
        worker,
        WorkerSchedule(0.1, 2, 5, 0.2),
        monotonic_clock=clock,
    ).run(stop)

    assert worker.cleanup.call_count == 2
    assert [call.args[0] for call in stop.wait.call_args_list] == [0.2, 0.1, 0.1]


def test_external_runtime_owns_metrics_lifecycle_even_when_loop_fails(
    mocker: MockerFixture,
) -> None:
    loop = mocker.Mock(spec=WorkerLoop)
    metrics = mocker.Mock(spec=MetricsHttpServer)
    runtime = ExternalWorkerRuntime(loop, metrics)
    stop = mocker.Mock()

    runtime.run(stop)
    metrics.start.assert_called_once_with()
    loop.run.assert_called_once_with(stop)
    metrics.stop.assert_called_once_with()

    metrics.reset_mock()
    loop.run.side_effect = RuntimeError("worker failed")
    with pytest.raises(RuntimeError, match="worker failed"):
        runtime.run(stop)
    metrics.start.assert_called_once_with()
    metrics.stop.assert_called_once_with()


@pytest.mark.parametrize(
    "values",
    ((0, 1, 1, 1), (1, 0, 1, 1), (1, 1, 0, 1), (1, 1, 1, 0), (True, 1, 1, 1)),
)
def test_worker_schedule_rejects_unbounded_values(
    values: tuple[float, int, int, float],
) -> None:
    with pytest.raises(ValueError):
        WorkerSchedule(*values)
