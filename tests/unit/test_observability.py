"""Deterministic observability, correlation, and metric safety tests."""

import json
import logging
from typing import Any, cast

import pytest
from pytest_mock import MockerFixture

from md_converter.observability import (
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


def test_correlation_accepts_safe_opaque_values_and_replaces_hostile_input() -> None:
    assert normalize_correlation_id("edge-request_42") == "edge-request_42"
    generated = normalize_correlation_id("../../private/source.md\nsecret=value")
    assert len(generated) == 32
    assert generated.isalnum()
    assert "source" not in generated
    with (
        pytest.raises(ValueError, match="Correlation"),
        correlated("../../private"),
    ):
        pass

    assert current_correlation_id() is None
    with correlated("request-1"):
        assert current_correlation_id() == "request-1"
    assert current_correlation_id() is None


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
    http_server = mocker.patch("md_converter.observability.ThreadingHTTPServer")
    thread = mocker.patch("md_converter.observability.Thread")
    http_server.return_value.server_address = ("127.0.0.1", 9464)
    server = MetricsHttpServer(OperationalMetrics(), queue, host="127.0.0.1", port=9464)

    server.start()
    assert server.address == ("127.0.0.1", 9464)
    thread.return_value.start.assert_called_once_with()
    with pytest.raises(RuntimeError, match="already running"):
        server.start()
    server.stop()
    http_server.return_value.shutdown.assert_called_once_with()
    http_server.return_value.server_close.assert_called_once_with()
    thread.return_value.join.assert_called_once_with()
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
