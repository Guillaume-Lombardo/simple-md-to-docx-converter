"""Real loopback HTTP coverage for external-worker process metrics."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from socket import create_connection
from threading import Event, Lock
from threading import enumerate as enumerate_threads
from time import monotonic, sleep
from typing import Any
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from markweave.observability import (
    MetricsHttpServer,
    MetricsServerError,
    OperationalMetrics,
    QueueSnapshot,
)

pytestmark = pytest.mark.integration


class _QueueObserver:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self._lock = Lock()
        self.calls = 0

    def observe_queue(
        self,
        now: datetime,
        *,
        timeout_seconds: float | None = None,
        cancelled: Event | None = None,
    ) -> QueueSnapshot:
        del now, timeout_seconds, cancelled
        with self._lock:
            self.calls += 1
        if self._fail:
            raise RuntimeError("private database detail")
        return QueueSnapshot(2, 3.5, 1)

    def cancel_observations(self, *, timeout_seconds: float | None = None) -> None:
        del timeout_seconds


class _BlockingQueueObserver(_QueueObserver):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.release = Event()

    def observe_queue(
        self,
        now: datetime,
        *,
        timeout_seconds: float | None = None,
        cancelled: Event | None = None,
    ) -> QueueSnapshot:
        self.entered.set()
        while not self.release.wait(0.01):
            if cancelled is not None and cancelled.is_set():
                raise RuntimeError("observation cancelled")
        return super().observe_queue(
            now, timeout_seconds=timeout_seconds, cancelled=cancelled
        )

    def cancel_observations(self, *, timeout_seconds: float | None = None) -> None:
        del timeout_seconds
        self.release.set()


def _get(url: str) -> tuple[int, str]:
    try:
        with urlopen(url, timeout=2) as response:  # noqa: S310 - fixed loopback test
            return response.status, response.read().decode()
    except HTTPError as error:
        return error.code, error.read().decode()


def test_worker_metrics_server_is_independently_and_concurrently_scrapeable() -> None:
    queue = _QueueObserver()
    metrics = OperationalMetrics()
    metrics.record_retry("worker_loop")
    server = MetricsHttpServer(
        metrics,
        queue,
        host="127.0.0.1",
        port=0,
        max_connections=16,
        observation_limit=16,
    )
    server.start()
    try:
        host, port = server.address
        url = f"http://{host}:{port}/metrics"
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = tuple(pool.map(_get, [url] * 16))
        assert all(status == 200 for status, _body in responses)
        assert all("md_converter_queue_depth 2" in body for _status, body in responses)
        assert all(
            "md_converter_worker_retries_total" in body for _status, body in responses
        )
        assert queue.calls == 16
        assert _get(f"http://{host}:{port}/private/path") == (404, "not found\n")
        with pytest.raises(RuntimeError, match="already running"):
            server.start()
    finally:
        server.stop()
        server.stop()


def test_worker_metrics_server_sanitizes_probe_and_bind_failures() -> None:
    failing = MetricsHttpServer(
        OperationalMetrics(), _QueueObserver(fail=True), host="127.0.0.1", port=0
    )
    failing.start()
    try:
        host, port = failing.address
        assert _get(f"http://{host}:{port}/metrics") == (
            503,
            "metrics unavailable\n",
        )
    finally:
        failing.stop()

    first = MetricsHttpServer(
        OperationalMetrics(), _QueueObserver(), host="127.0.0.1", port=0
    )
    first.start()
    try:
        host, port = first.address
        second = MetricsHttpServer(
            OperationalMetrics(), _QueueObserver(), host=host, port=port
        )
        with pytest.raises(MetricsServerError, match="listener failed") as caught:
            second.start()
        assert "address" not in repr(caught.value).casefold()
    finally:
        first.stop()


def test_worker_metrics_server_caps_database_observations() -> None:
    queue = _BlockingQueueObserver()
    server = MetricsHttpServer(
        OperationalMetrics(),
        queue,
        host="127.0.0.1",
        port=0,
        max_connections=2,
        observation_limit=1,
        request_timeout_seconds=0.5,
    )
    server.start()
    try:
        host, port = server.address
        url = f"http://{host}:{port}/metrics"
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(_get, url)
            assert queue.entered.wait(1)
            assert _get(url) == (503, "metrics unavailable\n")
            assert queue.calls == 0
            queue.release.set()
            assert first.result(timeout=1)[0] == 200
        assert queue.calls == 1
    finally:
        queue.release.set()
        server.stop()


def test_worker_metrics_server_stop_interrupts_a_truly_blocked_observation() -> None:
    queue = _BlockingQueueObserver()
    server = MetricsHttpServer(
        OperationalMetrics(),
        queue,
        host="127.0.0.1",
        port=0,
        max_connections=1,
        observation_limit=1,
        request_timeout_seconds=0.2,
    )
    server.start()
    host, port = server.address
    with ThreadPoolExecutor(max_workers=1) as pool:
        scrape = pool.submit(_get, f"http://{host}:{port}/metrics")
        assert queue.entered.wait(1)
        started = monotonic()
        server.stop()
        assert monotonic() - started < 1.0
        assert scrape.result(timeout=1)[0] in {200, 503}
    assert not any(
        thread.name.startswith("external-worker-metrics")
        for thread in enumerate_threads()
    )


def test_worker_metrics_server_enforces_slowloris_deadline_and_releases_capacity() -> (
    None
):
    server = MetricsHttpServer(
        OperationalMetrics(),
        _QueueObserver(),
        host="127.0.0.1",
        port=0,
        max_connections=1,
        observation_limit=1,
        accept_queue_size=1,
        request_timeout_seconds=0.12,
    )
    server.start()
    host, port = server.address
    slow = create_connection((host, port), timeout=1)
    started = monotonic()
    try:
        slow.sendall(b"GET /metrics HTTP/1.0\r\nX-Slow: ")
        while monotonic() - started < 0.3:
            try:
                slow.sendall(b"a")
            except OSError:
                break
            sleep(0.03)
        slow.settimeout(0.5)
        assert slow.recv(1) == b""
        assert monotonic() - started < 0.6
        assert _get(f"http://{host}:{port}/metrics")[0] == 200
    finally:
        slow.close()
        server.stop()
    assert not any(
        thread.name.startswith("external-worker-metrics")
        for thread in enumerate_threads()
    )


def test_worker_metrics_server_rejects_connections_when_workers_are_saturated() -> None:
    server = MetricsHttpServer(
        OperationalMetrics(),
        _QueueObserver(),
        host="127.0.0.1",
        port=0,
        max_connections=2,
        observation_limit=1,
        accept_queue_size=1,
        request_timeout_seconds=0.5,
    )
    server.start()
    host, port = server.address
    slow_connections = [create_connection((host, port), timeout=1) for _ in range(2)]
    try:
        for connection in slow_connections:
            connection.sendall(b"GET /metrics HTTP/1.0\r\nX-Slow: ")
        sleep(0.05)
        saturated = create_connection((host, port), timeout=1)
        try:
            saturated.sendall(b"GET /metrics HTTP/1.0\r\n\r\n")
            response = saturated.recv(512)
            assert b"503 Service Unavailable" in response
            assert b"metrics unavailable" in response
        finally:
            saturated.close()
    finally:
        for connection in slow_connections:
            connection.close()
        server.stop()
    assert not any(
        thread.name.startswith("external-worker-metrics")
        for thread in enumerate_threads()
    )


@pytest.mark.parametrize(
    ("host", "port"),
    [("", 9464), ("bad host", 9464), ("127.0.0.1", -1), ("127.0.0.1", 65_536)],
)
def test_worker_metrics_server_rejects_invalid_bind_configuration(
    host: str, port: int
) -> None:
    with pytest.raises(ValueError, match="Metrics bind"):
        MetricsHttpServer(OperationalMetrics(), _QueueObserver(), host=host, port=port)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_connections": 0},
        {"max_connections": 1, "observation_limit": 2},
        {"accept_queue_size": 0},
        {"request_timeout_seconds": float("inf")},
    ],
)
def test_worker_metrics_server_rejects_invalid_resource_limits(
    limits: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="Metrics server limits"):
        MetricsHttpServer(
            OperationalMetrics(),
            _QueueObserver(),
            host="127.0.0.1",
            port=0,
            **limits,
        )
