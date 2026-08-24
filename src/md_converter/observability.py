"""Content-free structured logging and bounded operational metrics."""

from __future__ import annotations

import json
import logging
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from time import monotonic
from typing import IO, Protocol
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_HEADER = "X-Correlation-ID"
MAX_CORRELATION_CHARACTERS = 128
_CORRELATION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_METRIC_METHODS = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
)
_correlation_id: ContextVar[str | None] = ContextVar(
    "md_converter_correlation_id", default=None
)
_SAFE_LOG_FIELDS = frozenset(
    {
        "correlation_id",
        "duration_seconds",
        "error_code",
        "job_id",
        "method",
        "operation",
        "owner_id",
        "state",
        "status_code",
        "step",
        "target_id",
        "version_id",
        "worker_id",
    }
)


def normalize_correlation_id(value: str | None) -> str:
    """Accept a safe opaque caller identifier or create an application identifier."""

    if value is not None and _CORRELATION_PATTERN.fullmatch(value):
        return value
    return uuid4().hex


def require_correlation_id(value: str) -> str:
    """Reject unsafe durable correlation identifiers without rewriting them."""

    if not _CORRELATION_PATTERN.fullmatch(value):
        raise ValueError("Correlation identifier is invalid")
    return value


def current_correlation_id() -> str | None:
    """Return the correlation identifier bound to the current execution context."""

    return _correlation_id.get()


def bind_correlation_id(value: str) -> Token[str | None]:
    """Bind a previously validated durable correlation identifier."""

    return _correlation_id.set(require_correlation_id(value))


def reset_correlation_id(token: Token[str | None]) -> None:
    """Restore the previous execution correlation context."""

    _correlation_id.reset(token)


@contextmanager
def correlated(value: str) -> Iterator[None]:
    """Bind correlation for one request or durable worker operation."""

    token = bind_correlation_id(value)
    try:
        yield
    finally:
        reset_correlation_id(token)


class JsonLogFormatter(logging.Formatter):
    """Serialize only the fixed, content-free application log vocabulary."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created)
            .astimezone()
            .isoformat(),
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id is None:
            correlation_id = current_correlation_id()
        if correlation_id is not None:
            payload["correlation_id"] = correlation_id
        for name in sorted(_SAFE_LOG_FIELDS - {"correlation_id"}):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_application_logging(stream: IO[str] | None = None) -> logging.Logger:
    """Configure the application logger once with deterministic JSON output."""

    logger = logging.getLogger("md_converter.application")
    if not logger.handlers:
        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


def log_event(event: str, *, level: int = logging.INFO, **fields: object) -> None:
    """Emit one fixed event without accepting document or request content fields."""

    if not event or not _CORRELATION_PATTERN.fullmatch(event):
        raise ValueError("Log event name is invalid")
    unsupported = fields.keys() - _SAFE_LOG_FIELDS
    if unsupported:
        raise ValueError("Unsupported structured log field")
    configure_application_logging().log(level, event, extra=fields)


class CorrelationMiddleware:
    """Bind safe request correlation and return it without reading request bodies."""

    def __init__(self, app: ASGIApp, *, metrics: OperationalMetrics) -> None:
        self._app = app
        self._metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        supplied = next(
            (
                value.decode("latin-1")
                for name, value in scope.get("headers", ())
                if name.lower() == CORRELATION_HEADER.lower().encode("ascii")
            ),
            None,
        )
        correlation_id = normalize_correlation_id(supplied)
        status_code = 500
        started = self._metrics.timer()

        async def correlated_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", ()))
                headers.append(
                    (
                        CORRELATION_HEADER.lower().encode("ascii"),
                        correlation_id.encode("ascii"),
                    )
                )
                message = {**message, "headers": headers}
            await send(message)

        with correlated(correlation_id):
            try:
                await self._app(scope, receive, correlated_send)
            finally:
                duration = max(0.0, self._metrics.timer() - started)
                method = str(scope.get("method", "UNKNOWN"))
                self._metrics.record_request(method, status_code, duration)
                log_event(
                    "http_request_completed",
                    method=method,
                    status_code=status_code,
                    duration_seconds=duration,
                )


@dataclass(frozen=True, slots=True)
class QueueSnapshot:
    """Low-cardinality queue gauges derived by one aggregate query."""

    depth: int
    oldest_age_seconds: float
    active_jobs: int


class QueueObserver(Protocol):
    """Cheap queue observation boundary shared by both SQL profiles."""

    def observe_queue(self, now: datetime) -> QueueSnapshot: ...


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Content-free durable audit record exposed only to administrators."""

    id: UUID
    actor_id: UUID
    owner_id: UUID
    operation: str
    target_id: UUID
    version_id: UUID | None
    administrator_intervention: bool
    created_at: datetime


class AuditReader(Protocol):
    """Bounded immutable-audit query port."""

    def list_recent(self, *, offset: int, limit: int) -> tuple[AuditRecord, ...]: ...


class OperationalMetrics:
    """Thread-safe low-cardinality counters and duration accumulators."""

    def __init__(self, *, monotonic_clock: Callable[[], float] = monotonic) -> None:
        self._lock = Lock()
        self._counters: defaultdict[tuple[str, tuple[tuple[str, str], ...]], float] = (
            defaultdict(float)
        )
        self._monotonic_clock = monotonic_clock

    def record_failure(self, code: str) -> None:
        self._increment("md_converter_job_failures_total", code=code)

    def record_saturation(self, scope: str) -> None:
        self._increment("md_converter_job_saturation_total", scope=scope)

    def record_expiration(self, count: int) -> None:
        self._increment("md_converter_job_expirations_total", amount=count)

    def record_retry(self, operation: str) -> None:
        self._increment("md_converter_worker_retries_total", operation=operation)

    def record_recovery(self, count: int) -> None:
        self._increment("md_converter_job_recoveries_total", amount=count)

    def record_step_duration(self, step: str, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Step duration must not be negative")
        self._increment("md_converter_job_step_duration_seconds_count", step=step)
        self._increment(
            "md_converter_job_step_duration_seconds_sum", amount=seconds, step=step
        )

    def record_request(self, method: str, status_code: int, seconds: float) -> None:
        status = str(status_code)
        method = method if method in _METRIC_METHODS else "OTHER"
        self._increment(
            "md_converter_http_requests_total", method=method, status=status
        )
        self._increment(
            "md_converter_http_request_duration_seconds_sum",
            amount=max(0.0, seconds),
            method=method,
            status=status,
        )

    def timer(self) -> float:
        return self._monotonic_clock()

    def render(self, queue: QueueSnapshot) -> str:
        """Render Prometheus text format without identifiers or unbounded labels."""

        gauges = (
            ("md_converter_queue_depth", float(queue.depth)),
            ("md_converter_queue_oldest_age_seconds", queue.oldest_age_seconds),
            ("md_converter_active_jobs", float(queue.active_jobs)),
        )
        with self._lock:
            counters = tuple(sorted(self._counters.items()))
        lines = [f"{name} {value:g}" for name, value in gauges]
        for (name, labels), value in counters:
            rendered_labels = ""
            if labels:
                rendered_labels = (
                    "{"
                    + ",".join(
                        f'{key}="{_escape_label(label)}"' for key, label in labels
                    )
                    + "}"
                )
            lines.append(f"{name}{rendered_labels} {value:g}")
        return "\n".join(lines) + "\n"

    def _increment(self, name: str, *, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError("Metric increments must not be negative")
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._counters[key] += amount


def _escape_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')
