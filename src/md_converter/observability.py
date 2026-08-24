"""Content-free structured logging and bounded operational metrics."""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BufferedIOBase
from socket import SHUT_RDWR, socket
from threading import BoundedSemaphore, Event, Lock, Thread
from time import monotonic
from typing import IO, Any, Protocol
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_HEADER = "X-Correlation-ID"
MAX_CORRELATION_CHARACTERS = 128
MAX_LOG_DURATION_SECONDS = 365 * 24 * 60 * 60
MIN_HTTP_STATUS = 100
MAX_HTTP_STATUS = 599
MAX_TCP_PORT = 65_535
_CORRELATION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_METRIC_METHODS = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
)
_LOG_METHODS = _METRIC_METHODS | {"OTHER"}
_LOG_EVENTS = frozenset(
    {
        "http_request_completed",
        "job_expiration_completed",
        "job_processing_completed",
        "job_processing_failed",
        "job_processing_started",
        "job_recovery_completed",
        "readiness_failed",
        "worker_retry_scheduled",
    }
)
_LOG_OPERATIONS = frozenset({"lease_recovery", "retention_cleanup", "worker_loop"})
_LOG_STATES = frozenset(
    {"queued", "running", "succeeded", "failed", "cancelled", "expired"}
)
_LOG_STEPS = frozenset(
    {"queued", "validating", "rendering", "docx", "pdf", "publishing", "complete"}
)
_LOG_ERROR_CODES = frozenset(
    {
        "validation",
        "workspace_failure",
        "pandoc_unavailable",
        "pandoc_timeout",
        "pandoc_failure",
        "invalid_docx",
        "mermaid_unavailable",
        "mermaid_timeout",
        "mermaid_failure",
        "invalid_mermaid_output",
        "libreoffice_unavailable",
        "pdf_timeout",
        "pdf_cancelled",
        "pdf_failure",
        "pdf_limit_exceeded",
        "invalid_pdf",
        "template_integrity",
        "source_integrity",
        "resource_budget_exceeded",
    }
)
_LOG_UUID_FIELDS = frozenset({"job_id", "owner_id", "target_id", "version_id"})
_WORKER_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
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
    """Create a server identifier without reflecting caller-controlled text."""

    # Inspecting the header remains deliberately side-effect free: even a syntactically
    # valid UUID must never become a logged or durable application identifier.
    if value is not None:
        _valid_correlation_id(value)
    return str(uuid4())


def require_correlation_id(value: str) -> str:
    """Reject unsafe durable correlation identifiers without rewriting them."""

    if ".." in value or not _CORRELATION_PATTERN.fullmatch(value):
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
            "event": _safe_log_event(record.getMessage()),
        }
        correlation_id = getattr(record, "correlation_id", None)
        if correlation_id is None:
            correlation_id = current_correlation_id()
        if isinstance(correlation_id, str) and _valid_correlation_id(correlation_id):
            payload["correlation_id"] = correlation_id
        for name in sorted(_SAFE_LOG_FIELDS - {"correlation_id"}):
            value = getattr(record, name, None)
            if value is not None:
                try:
                    payload[name] = _validate_log_field(name, value)
                except ValueError:
                    continue
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

    if event not in _LOG_EVENTS:
        raise ValueError("Log event name is invalid")
    unsupported = fields.keys() - _SAFE_LOG_FIELDS
    if unsupported:
        raise ValueError("Unsupported structured log field")
    if level not in {logging.INFO, logging.WARNING, logging.ERROR}:
        raise ValueError("Log level is invalid")
    validated = {
        name: _validate_log_field(name, value) for name, value in fields.items()
    }
    configure_application_logging().log(level, event, extra=validated)


class CorrelationMiddleware:
    """Bind server-generated request correlation without reading request bodies."""

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
                method = _normalize_method(scope.get("method"))
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

    def observe_queue(
        self,
        now: datetime,
        *,
        timeout_seconds: float | None = None,
        cancelled: Event | None = None,
    ) -> QueueSnapshot: ...

    def cancel_observations(self, *, timeout_seconds: float | None = None) -> None:
        """Interrupt every active database observation without leaking workers."""
        ...


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """Content-free durable audit record exposed only to administrators."""

    id: UUID
    actor_id: UUID
    owner_id: UUID
    operation: str
    target_id: UUID
    target_type: str
    target_version: str | None
    version_id: UUID | None
    administrator_intervention: bool
    created_at: datetime


class AuditReader(Protocol):
    """Bounded immutable-audit query port."""

    def list_recent(self, *, offset: int, limit: int) -> tuple[AuditRecord, ...]: ...


class MetricsServerError(RuntimeError):
    """Sanitized external-worker metrics listener failure."""


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
        method = _normalize_method(method)
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


def require_worker_id(value: str) -> str:
    """Require a bounded path-free identifier safe for leases and logs."""

    if not _WORKER_ID_PATTERN.fullmatch(value):
        raise ValueError("Worker identifier is invalid")
    return value


def _valid_correlation_id(value: str) -> bool:
    return ".." not in value and _CORRELATION_PATTERN.fullmatch(value) is not None


def _safe_log_event(value: str) -> str:
    return value if value in _LOG_EVENTS else "invalid_log_event"


def _normalize_method(value: object) -> str:
    return value if isinstance(value, str) and value in _METRIC_METHODS else "OTHER"


def _validate_log_field(name: str, value: object) -> object:
    if name == "correlation_id":
        if not isinstance(value, str) or not _valid_correlation_id(value):
            raise ValueError("Log correlation identifier is invalid")
        return value
    if name in _LOG_UUID_FIELDS:
        return _validate_uuid_log_value(value)
    if name == "worker_id":
        if not isinstance(value, str):
            raise ValueError("Log worker identifier is invalid")
        return require_worker_id(value)
    fixed_values = {
        "method": _LOG_METHODS,
        "operation": _LOG_OPERATIONS,
        "state": _LOG_STATES,
        "step": _LOG_STEPS,
        "error_code": _LOG_ERROR_CODES,
    }
    if name in fixed_values:
        if not isinstance(value, str) or value not in fixed_values[name]:
            raise ValueError("Log enum field is invalid")
        return value
    if name in {"status_code", "duration_seconds"}:
        return _validate_numeric_log_value(name, value)
    raise ValueError("Unsupported structured log field")


def _validate_uuid_log_value(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Log UUID field is invalid")
    try:
        parsed = UUID(value)
    except ValueError:
        raise ValueError("Log UUID field is invalid") from None
    if str(parsed) != value:
        raise ValueError("Log UUID field is invalid")
    return value


def _validate_numeric_log_value(name: str, value: object) -> int | float:
    if name == "status_code":
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not MIN_HTTP_STATUS <= value <= MAX_HTTP_STATUS
        ):
            raise ValueError("Log status code is invalid")
        return value
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not 0 <= value <= MAX_LOG_DURATION_SECONDS
    ):
        raise ValueError("Log duration is invalid")
    return value


class MetricsHttpServer:
    """Lifecycle-owned scrape surface for one external worker process."""

    def __init__(  # noqa: PLR0913 - every listener resource bound is explicit
        self,
        metrics: OperationalMetrics,
        queue: QueueObserver,
        *,
        host: str,
        port: int,
        max_connections: int = 4,
        observation_limit: int = 2,
        accept_queue_size: int = 8,
        request_timeout_seconds: float = 2.0,
    ) -> None:
        if not host or any(character.isspace() for character in host):
            raise ValueError("Metrics bind host is invalid")
        if isinstance(port, bool) or not 0 <= port <= MAX_TCP_PORT:
            raise ValueError("Metrics bind port is invalid")
        if (
            isinstance(max_connections, bool)
            or max_connections <= 0
            or isinstance(observation_limit, bool)
            or not 0 < observation_limit <= max_connections
            or isinstance(accept_queue_size, bool)
            or accept_queue_size <= 0
            or isinstance(request_timeout_seconds, bool)
            or not math.isfinite(request_timeout_seconds)
            or request_timeout_seconds <= 0
        ):
            raise ValueError("Metrics server limits are invalid")
        self._metrics = metrics
        self._queue = queue
        self._host = host
        self._port = port
        self._max_connections = max_connections
        self._observation_limit = observation_limit
        self._accept_queue_size = accept_queue_size
        self._request_timeout_seconds = request_timeout_seconds
        self._lock = Lock()
        self._stopping = Event()
        self._server: _BoundedMetricsHttpServer | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        with self._lock:
            if self._server is not None:
                raise RuntimeError("Metrics server is already running")
            handler = self._handler()
            try:
                server = _BoundedMetricsHttpServer(
                    (self._host, self._port),
                    handler,
                    max_connections=self._max_connections,
                    accept_queue_size=self._accept_queue_size,
                    request_timeout_seconds=self._request_timeout_seconds,
                )
            except OSError:
                raise MetricsServerError("Metrics listener failed to start") from None
            thread = Thread(
                target=server.serve_forever,
                name="external-worker-metrics",
                daemon=False,
            )
            self._server = server
            self._thread = thread
            self._stopping.clear()
            thread.start()

    def stop(self) -> None:
        with self._lock:
            server = self._server
            thread = self._thread
        if server is None or thread is None:
            return
        self._stopping.set()
        self._queue.cancel_observations(timeout_seconds=self._request_timeout_seconds)
        server.shutdown()
        server.server_close()
        thread.join(self._request_timeout_seconds + 1.0)
        if thread.is_alive():
            raise MetricsServerError("Metrics listener failed to stop")
        with self._lock:
            if self._server is server:
                self._server = None
                self._thread = None

    @property
    def address(self) -> tuple[str, int]:
        with self._lock:
            if self._server is None:
                raise RuntimeError("Metrics server is not running")
            host, port = self._server.server_address[:2]
            return str(host), int(port)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        metrics = self._metrics
        queue = self._queue
        observations = BoundedSemaphore(self._observation_limit)
        request_timeout_seconds = self._request_timeout_seconds
        stopping = self._stopping

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def setup(self) -> None:
                super().setup()
                deadline = monotonic() + request_timeout_seconds
                self.connection.settimeout(request_timeout_seconds)
                self.rfile.close()
                self.rfile = _DeadlineReader(self.connection, deadline)

            def do_GET(self) -> None:
                if self.path != "/metrics":
                    self._respond(404, b"not found\n", "text/plain")
                    return
                if not observations.acquire(blocking=False):
                    self._respond(503, b"metrics unavailable\n", "text/plain")
                    return
                try:
                    payload = metrics.render(
                        queue.observe_queue(
                            datetime.now(UTC),
                            timeout_seconds=request_timeout_seconds,
                            cancelled=stopping,
                        )
                    ).encode()
                except Exception:  # exporter failures remain local and content-free
                    self._respond(503, b"metrics unavailable\n", "text/plain")
                    return
                finally:
                    observations.release()
                self._respond(
                    200,
                    payload,
                    "text/plain; version=0.0.4; charset=utf-8",
                )

            def _respond(self, status: int, payload: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(payload)
                self.close_connection = True

            def log_message(self, format: str, *args: object) -> None:
                del format, args

        return Handler


class _DeadlineReader(BufferedIOBase):
    """Apply one absolute deadline across request-line and header reads."""

    def __init__(self, connection: socket, deadline: float) -> None:
        super().__init__()
        self._connection = connection
        self._deadline = deadline

    def readline(self, size: int | None = -1) -> bytes:
        resolved_size = -1 if size is None else size
        result = bytearray()
        while resolved_size < 0 or len(result) < resolved_size:
            item = self._receive_one()
            if not item:
                break
            result.extend(item)
            if item == b"\n":
                break
        return bytes(result)

    def read(self, size: int | None = -1) -> bytes:
        resolved_size = -1 if size is None else size
        if resolved_size < 0:
            raise ValueError("Bounded request reads require a size")
        result = bytearray()
        while len(result) < resolved_size:
            item = self._receive_one()
            if not item:
                break
            result.extend(item)
        return bytes(result)

    def readable(self) -> bool:
        return True

    def _receive_one(self) -> bytes:
        remaining = self._deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError
        self._connection.settimeout(remaining)
        return self._connection.recv(1)


class _BoundedMetricsHttpServer(HTTPServer):
    """HTTP server with fixed request concurrency and no executor backlog."""

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        max_connections: int,
        accept_queue_size: int,
        request_timeout_seconds: float,
    ) -> None:
        self.request_queue_size = accept_queue_size
        self._request_timeout_seconds = request_timeout_seconds
        self._admission = BoundedSemaphore(max_connections)
        self._executor = ThreadPoolExecutor(
            max_workers=max_connections,
            thread_name_prefix="external-worker-metrics-request",
        )
        super().__init__(server_address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._admission.acquire(blocking=False):
            self._reject_saturated(request)
            self.shutdown_request(request)
            return
        try:
            self._executor.submit(self._process_admitted, request, client_address)
        except RuntimeError:
            self._admission.release()
            self.shutdown_request(request)

    def server_close(self) -> None:
        super().server_close()
        self._executor.shutdown(wait=True, cancel_futures=True)

    def handle_error(self, request: Any, client_address: Any) -> None:
        del request, client_address

    def _process_admitted(self, request: socket, client_address: object) -> None:
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self._admission.release()

    def _reject_saturated(self, request: socket) -> None:
        try:
            request.settimeout(min(self._request_timeout_seconds, 0.1))
            request.sendall(
                b"HTTP/1.0 503 Service Unavailable\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 20\r\n"
                b"Connection: close\r\n\r\n"
                b"metrics unavailable\n"
            )
            request.shutdown(SHUT_RDWR)
        except OSError:
            pass
