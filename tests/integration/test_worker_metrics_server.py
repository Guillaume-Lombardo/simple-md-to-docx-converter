"""Real loopback HTTP coverage for external-worker process metrics."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from md_converter.observability import (
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

    def observe_queue(self, now: datetime) -> QueueSnapshot:
        del now
        with self._lock:
            self.calls += 1
        if self._fail:
            raise RuntimeError("private database detail")
        return QueueSnapshot(2, 3.5, 1)


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
    server = MetricsHttpServer(metrics, queue, host="127.0.0.1", port=0)
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


@pytest.mark.parametrize(
    ("host", "port"),
    [("", 9464), ("bad host", 9464), ("127.0.0.1", -1), ("127.0.0.1", 65_536)],
)
def test_worker_metrics_server_rejects_invalid_bind_configuration(
    host: str, port: int
) -> None:
    with pytest.raises(ValueError, match="Metrics bind"):
        MetricsHttpServer(OperationalMetrics(), _QueueObserver(), host=host, port=port)
