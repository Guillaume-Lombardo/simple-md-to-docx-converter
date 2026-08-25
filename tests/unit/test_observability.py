"""Deterministic observability, correlation, and metric safety tests."""

import asyncio
import json
import logging
from typing import Any, cast
from uuid import UUID

import pytest
from pytest_mock import MockerFixture
from starlette.types import Message

from md_converter import observability
from md_converter.observability import (
    CORRELATION_STATE_KEY,
    CorrelationMiddleware,
    JsonLogFormatter,
    MetricsHttpServer,
    MetricsServerError,
    OperationalMetrics,
    QueueObserver,
    QueueSnapshot,
    correlated,
    current_correlation_id,
    log_event,
    normalize_correlation_id,
    require_worker_id,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "supplied",
    [
        None,
        "secret-token-value",
        "quarterly-results.docx",
        "Bearer-private-credential",
        "00000000-0000-4000-8000-000000000001",
        "../../private/source.md\nsecret=value",
    ],
)
def test_correlation_is_server_generated_for_every_caller_value(
    supplied: str | None,
) -> None:
    generated = normalize_correlation_id(supplied)
    assert str(UUID(generated)) == generated
    assert UUID(generated).version == 4
    assert generated != supplied
    if supplied is not None:
        assert supplied not in generated


def test_internal_correlation_context_rejects_hostile_values() -> None:
    with (
        pytest.raises(ValueError, match="Correlation"),
        correlated("../../private"),
    ):
        pass

    assert current_correlation_id() is None
    with correlated("request-1"):
        assert current_correlation_id() == "request-1"
    assert current_correlation_id() is None


def test_correlation_is_explicitly_isolated_in_concurrent_request_scopes() -> None:
    async def exercise() -> list[tuple[str, str]]:
        arrived = 0
        both_arrived = asyncio.Event()
        scope_correlations: dict[str, str] = {}

        async def downstream(scope: Any, receive: Any, send: Any) -> None:
            nonlocal arrived
            request_name = str(scope["path"])
            scope_correlations[request_name] = scope["state"][CORRELATION_STATE_KEY]
            arrived += 1
            if arrived == 2:
                both_arrived.set()
            await asyncio.wait_for(both_arrived.wait(), timeout=1.0)
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        middleware = CorrelationMiddleware(downstream, metrics=OperationalMetrics())

        async def request(path: str) -> tuple[str, str]:
            messages: list[Message] = []

            async def receive() -> Message:
                return {"type": "http.disconnect"}

            async def send(message: Message) -> None:
                messages.append(message)

            await middleware(
                {"type": "http", "method": "POST", "path": path, "headers": []},
                receive,
                send,
            )
            response_start = next(
                message
                for message in messages
                if message["type"] == "http.response.start"
            )
            response_correlation = dict(response_start["headers"])[
                b"x-correlation-id"
            ].decode("ascii")
            return scope_correlations[path], response_correlation

        return list(await asyncio.gather(request("/first"), request("/second")))

    correlations = asyncio.run(exercise())
    assert all(scope_id == header_id for scope_id, header_id in correlations)
    assert len({scope_id for scope_id, _header_id in correlations}) == 2


def test_json_formatter_emits_only_allowlisted_content_free_fields() -> None:
    record = logging.LogRecord(
        "md_converter.application",
        logging.INFO,
        __file__,
        1,
        "job_processing_failed",
        (),
        None,
    )
    record.correlation_id = "request-1"
    record.job_id = "00000000-0000-0000-0000-000000000001"
    record.error_code = "invalid_docx"
    record.filename = "private.md"
    record.__dict__["private_value"] = "do-not-print"
    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["event"] == "job_processing_failed"
    assert payload["correlation_id"] == "request-1"
    assert payload["error_code"] == "invalid_docx"
    assert "filename" not in payload
    assert "private_value" not in payload
    assert "private.md" not in json.dumps(payload)

    with pytest.raises(ValueError, match="Unsupported"):
        log_event("readiness_failed", content="private markdown")
    with pytest.raises(ValueError, match="event"):
        log_event("")


def test_metrics_are_low_cardinality_and_cover_required_operational_signals() -> None:
    metrics = OperationalMetrics(monotonic_clock=lambda: 12.5)
    metrics.record_failure("invalid_docx")
    metrics.record_saturation("owner")
    metrics.record_expiration(2)
    metrics.record_retry("worker_loop")
    metrics.record_recovery(3)
    metrics.record_step_duration("docx", 1.25)
    metrics.record_request("POST", 202, 0.5)
    metrics.record_request("ATTACKER-CONTROLLED-METHOD", 400, 0.1)

    rendered = metrics.render(QueueSnapshot(4, 8.5, 2))
    assert "md_converter_queue_depth 4" in rendered
    assert "md_converter_queue_oldest_age_seconds 8.5" in rendered
    assert "md_converter_active_jobs 2" in rendered
    assert 'md_converter_job_failures_total{code="invalid_docx"} 1' in rendered
    assert 'md_converter_job_saturation_total{scope="owner"} 1' in rendered
    assert "md_converter_job_expirations_total 2" in rendered
    assert 'md_converter_worker_retries_total{operation="worker_loop"} 1' in rendered
    assert "md_converter_job_recoveries_total 3" in rendered
    assert 'md_converter_job_step_duration_seconds_sum{step="docx"} 1.25' in rendered
    assert 'method="OTHER",status="400"' in rendered
    assert "ATTACKER-CONTROLLED-METHOD" not in rendered
    assert "00000000" not in rendered

    with pytest.raises(ValueError, match="negative"):
        metrics.record_step_duration("pdf", -1)
    with pytest.raises(ValueError, match="negative"):
        metrics.record_expiration(-1)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("correlation_id", "../private"),
        ("job_id", "../../document.docx"),
        ("owner_id", "00000000-0000-0000-0000-000000000001\nsecret"),
        ("version_id", "0" * 10_000),
        ("worker_id", "../worker"),
        ("worker_id", "w" * 65),
        ("method", "GET\nprivate"),
        ("operation", "read/private/path"),
        ("state", "private-state"),
        ("step", "../../step"),
        ("error_code", "private-error"),
        ("status_code", 99),
        ("status_code", 600),
        ("status_code", True),
        ("duration_seconds", -1.0),
        ("duration_seconds", float("nan")),
        ("duration_seconds", 31_536_001),
    ],
)
def test_structured_log_values_reject_hostile_or_unbounded_data(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError, match=r"Log|Worker"):
        cast(Any, log_event)("readiness_failed", **{field: value})


def test_formatter_replaces_unsafe_event_and_drops_unsafe_allowlisted_values() -> None:
    record = logging.LogRecord(
        "md_converter.application",
        logging.INFO,
        __file__,
        1,
        "private document content /absolute/path",
        (),
        None,
    )
    record.worker_id = "../../private"
    record.method = "GET\nsecret"
    payload = json.loads(JsonLogFormatter().format(record))
    assert payload["event"] == "invalid_log_event"
    assert "worker_id" not in payload
    assert "method" not in payload
    assert "private" not in json.dumps(payload)
    assert require_worker_id("worker_01-safe") == "worker_01-safe"

    with pytest.raises(ValueError, match="event"):
        log_event("private_event")
    with pytest.raises(ValueError, match="level"):
        log_event("readiness_failed", level=logging.DEBUG)


def test_metrics_server_lifecycle_and_bind_failure_are_sanitized(
    mocker: MockerFixture,
) -> None:
    queue = mocker.Mock(spec=QueueObserver)
    http_server = mocker.patch("md_converter.observability._BoundedMetricsHttpServer")
    thread = mocker.patch("md_converter.observability.Thread")
    http_server.return_value.server_address = ("127.0.0.1", 9464)
    thread.return_value.is_alive.return_value = False
    server = MetricsHttpServer(OperationalMetrics(), queue, host="127.0.0.1", port=9464)

    server.start()
    assert server.address == ("127.0.0.1", 9464)
    thread.return_value.start.assert_called_once_with()
    with pytest.raises(RuntimeError, match="already running"):
        server.start()
    server.stop()
    http_server.return_value.shutdown.assert_called_once_with()
    queue.cancel_observations.assert_called_once_with(timeout_seconds=2.0)
    http_server.return_value.server_close.assert_called_once_with()
    thread.return_value.join.assert_called_once_with(3.0)
    server.stop()
    with pytest.raises(RuntimeError, match="not running"):
        _ = server.address

    http_server.side_effect = OSError("private bind detail")
    failing = MetricsHttpServer(
        OperationalMetrics(), queue, host="127.0.0.1", port=9464
    )
    with pytest.raises(MetricsServerError, match="listener failed") as caught:
        failing.start()
    assert "private" not in repr(caught.value)

    for host, port in (("", 1), ("bad host", 1), ("127.0.0.1", True)):
        with pytest.raises(ValueError, match="Metrics bind"):
            MetricsHttpServer(OperationalMetrics(), queue, host=host, port=port)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_connections": True},
        {"max_connections": 0},
        {"observation_limit": True},
        {"observation_limit": 0},
        {"max_connections": 1, "observation_limit": 2},
        {"accept_queue_size": True},
        {"accept_queue_size": 0},
        {"request_timeout_seconds": True},
        {"request_timeout_seconds": float("inf")},
        {"request_timeout_seconds": 0},
    ],
)
def test_metrics_server_rejects_every_invalid_limit(limits: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match="server limits"):
        MetricsHttpServer(
            OperationalMetrics(),
            cast(QueueObserver, object()),
            host="127.0.0.1",
            port=9464,
            **limits,
        )


def test_deadline_reader_enforces_absolute_budget_and_bounded_reads(
    mocker: MockerFixture,
) -> None:
    connection = mocker.Mock()
    connection.recv.side_effect = [b"a", b"\n"]
    mocker.patch("md_converter.observability.monotonic", return_value=1.0)
    reader = observability._DeadlineReader(connection, 2.0)
    assert reader.readline() == b"a\n"
    assert reader.readable()
    assert connection.settimeout.call_count == 2

    connection.recv.side_effect = [b"z"]
    assert observability._DeadlineReader(connection, 2.0).readline(1) == b"z"
    connection.recv.side_effect = [b""]
    assert observability._DeadlineReader(connection, 2.0).readline() == b""

    connection.reset_mock()
    connection.recv.side_effect = [b"x", b""]
    reader = observability._DeadlineReader(connection, 2.0)
    assert reader.read(2) == b"x"
    connection.recv.side_effect = [b"x", b"y"]
    assert observability._DeadlineReader(connection, 2.0).read(2) == b"xy"
    with pytest.raises(ValueError, match="require a size"):
        reader.read()

    mocker.patch("md_converter.observability.monotonic", return_value=3.0)
    with pytest.raises(TimeoutError):
        observability._DeadlineReader(connection, 2.0).readline(1)


def test_bounded_metrics_server_admission_and_failure_paths(
    mocker: MockerFixture,
) -> None:
    server = object.__new__(observability._BoundedMetricsHttpServer)
    server._admission = mocker.Mock()
    server._executor = mocker.Mock()
    server._request_timeout_seconds = 0.5
    server.shutdown_request = mocker.Mock()
    server._reject_saturated = mocker.Mock()
    request = mocker.Mock()
    address = ("127.0.0.1", 1)

    server._admission.acquire.return_value = False
    server.process_request(request, address)
    server._reject_saturated.assert_called_once_with(request)
    server.shutdown_request.assert_called_once_with(request)

    server._admission.acquire.return_value = True
    server.process_request(request, address)
    server._executor.submit.assert_called_once_with(
        server._process_admitted, request, address
    )

    server._executor.submit.side_effect = RuntimeError
    server.process_request(request, address)
    server._admission.release.assert_called_once_with()

    server.finish_request = mocker.Mock(side_effect=RuntimeError)
    server.handle_error = mocker.Mock()
    server.shutdown_request.reset_mock()
    server._admission.release.reset_mock()
    server._process_admitted(request, address)
    server.handle_error.assert_called_once_with(request, address)
    server.shutdown_request.assert_called_once_with(request)
    server._admission.release.assert_called_once_with()

    server.finish_request.side_effect = None
    server.handle_error.reset_mock()
    server._process_admitted(request, address)
    server.handle_error.assert_not_called()


def test_bounded_metrics_server_saturation_response_is_safe(
    mocker: MockerFixture,
) -> None:
    server = object.__new__(observability._BoundedMetricsHttpServer)
    server._request_timeout_seconds = 0.5
    request = mocker.Mock()
    server._reject_saturated(request)
    request.settimeout.assert_called_once_with(0.1)
    assert b"503 Service Unavailable" in request.sendall.call_args.args[0]
    assert b"metrics unavailable" in request.sendall.call_args.args[0]

    request.sendall.side_effect = OSError
    server._reject_saturated(request)
