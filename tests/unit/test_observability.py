"""Deterministic observability, correlation, and metric safety tests."""

import json
import logging

import pytest

from md_converter.observability import (
    JsonLogFormatter,
    OperationalMetrics,
    QueueSnapshot,
    correlated,
    current_correlation_id,
    log_event,
    normalize_correlation_id,
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
        log_event("unsafe_event", content="private markdown")
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
