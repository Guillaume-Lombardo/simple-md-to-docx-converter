"""Bounded mTLS client and server for the node-local Kubernetes attester."""

from __future__ import annotations

import hashlib
import http.client
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from math import isfinite
from pathlib import Path
from socketserver import ThreadingMixIn
from threading import BoundedSemaphore
from typing import Any, cast
from uuid import UUID

import markweave.broker.kubernetes_attester_protocol as _kubernetes_attester_protocol
import markweave.broker.kubernetes_attester_service as _kubernetes_attester_service
from markweave.broker.kubernetes_runtime import (
    KubernetesAttestationContract,
    KubernetesNodeAttester,
    KubernetesPodIdentity,
    KubernetesRuntimeError,
    KubernetesRuntimeUnit,
    KubernetesSandboxIdentity,
)
from markweave.broker.models import (
    EvidenceDigest,
    ManagedUnitState,
    RuntimeIncarnation,
)

_PROTOCOL = _kubernetes_attester_protocol._PROTOCOL
_VERSION = _kubernetes_attester_protocol._VERSION
_MAX_NODE_NAME_BYTES = _kubernetes_attester_service._MAX_NODE_NAME_BYTES
NodeAttesterService = _kubernetes_attester_service.NodeAttesterService
_encode = _kubernetes_attester_protocol._encode
_decode = _kubernetes_attester_protocol._decode
_pod_mapping = _kubernetes_attester_protocol._pod_mapping
_pod = _kubernetes_attester_protocol._pod
_sandbox_mapping = _kubernetes_attester_protocol._sandbox_mapping
_sandbox = _kubernetes_attester_protocol._sandbox
_contract_mapping = _kubernetes_attester_protocol._contract_mapping
_contract = _kubernetes_attester_protocol._contract
_integers = _kubernetes_attester_protocol._integers
_closed_mapping = _kubernetes_attester_protocol._closed_mapping
_required_text = _kubernetes_attester_protocol._required_text
_same_runtime_identity = _kubernetes_attester_protocol._same_runtime_identity

_PATH = "/v1/attest"

_CONTENT_TYPE = "application/json"

_HTTP_OK = 200

_HTTP_BAD_REQUEST = 400

_HTTP_FORBIDDEN = 403

_HTTP_METHOD_NOT_ALLOWED = 405

_HTTP_PAYLOAD_TOO_LARGE = 413

_HTTP_UNSUPPORTED_MEDIA = 415

_MAX_PORT = 65535


@dataclass(frozen=True, slots=True)
class AttesterTransportLimits:
    """Hard framing and timing limits shared by both peers."""

    max_request_bytes: int
    max_response_bytes: int
    timeout_seconds: float
    max_concurrent_requests: int

    def __post_init__(self) -> None:
        if (
            type(self.max_request_bytes) is not int
            or self.max_request_bytes <= 0
            or type(self.max_response_bytes) is not int
            or self.max_response_bytes <= 0
            or type(self.timeout_seconds) not in {int, float}
            or not isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or type(self.max_concurrent_requests) is not int
            or self.max_concurrent_requests <= 0
        ):
            raise ValueError("Kubernetes attester transport limits are invalid")


@dataclass(frozen=True, slots=True)
class AttesterReadinessPolicy:
    """Bound retries of the one explicit sandbox-not-yet-observable outcome."""

    timeout_seconds: float
    poll_interval_seconds: float

    def __post_init__(self) -> None:
        if (
            type(self.timeout_seconds) not in {int, float}
            or not isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or type(self.poll_interval_seconds) not in {int, float}
            or not isfinite(self.poll_interval_seconds)
            or self.poll_interval_seconds <= 0
            or self.poll_interval_seconds > self.timeout_seconds
        ):
            raise ValueError("Kubernetes attester readiness policy is invalid")


@dataclass(frozen=True, slots=True)
class AttesterClientTlsConfig:
    """Broker-only client identity and attester-server trust policy."""

    port: int
    certificate_file: Path
    private_key_file: Path
    server_ca_file: Path
    expected_server_certificate: EvidenceDigest

    def __post_init__(self) -> None:
        if (
            type(self.port) is not int
            or not 1 <= self.port <= _MAX_PORT
            or any(
                not isinstance(value, Path)
                for value in (
                    self.certificate_file,
                    self.private_key_file,
                    self.server_ca_file,
                )
            )
            or type(self.expected_server_certificate) is not EvidenceDigest
        ):
            raise ValueError("Kubernetes attester client TLS configuration is invalid")


@dataclass(frozen=True, slots=True)
class AttesterServerTlsConfig:
    """Node-local listener identity and exact broker peer authorization."""

    listen_host: str
    port: int
    certificate_file: Path
    private_key_file: Path
    client_ca_file: Path
    expected_client_certificate: EvidenceDigest

    def __post_init__(self) -> None:
        if (
            type(self.listen_host) is not str
            or not self.listen_host
            or type(self.port) is not int
            or not 0 <= self.port <= _MAX_PORT
            or any(
                not isinstance(value, Path)
                for value in (
                    self.certificate_file,
                    self.private_key_file,
                    self.client_ca_file,
                )
            )
            or type(self.expected_client_certificate) is not EvidenceDigest
        ):
            raise ValueError("Kubernetes attester server TLS configuration is invalid")


class HttpsNodeAttesterClient(KubernetesNodeAttester):
    """Route each request only to the node named by the scheduled Pod."""

    def __init__(
        self,
        tls: AttesterClientTlsConfig,
        limits: AttesterTransportLimits,
        readiness: AttesterReadinessPolicy,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            type(tls) is not AttesterClientTlsConfig
            or type(limits) is not AttesterTransportLimits
            or type(readiness) is not AttesterReadinessPolicy
        ):
            raise ValueError("Kubernetes attester client is invalid")
        self._tls = tls
        self._limits = limits
        self._readiness = readiness
        self._monotonic = monotonic
        self._sleep = sleep
        self._context = _client_context(tls)
        self._bound: dict[UUID, KubernetesRuntimeUnit] = {}

    def bind(
        self, pod: KubernetesPodIdentity, contract: KubernetesAttestationContract
    ) -> KubernetesSandboxIdentity:
        request = {
            "contract": _contract_mapping(contract),
            "operation": "bind",
            "pod": _pod_mapping(pod),
            "protocol": _PROTOCOL,
            "version": _VERSION,
        }
        response = self._exchange_until_ready(
            pod.node_name,
            request,
            timeout_message="Kubernetes sandbox readiness timed out",
        )
        if set(response) != {"outcome", "sandbox"} or response["outcome"] != "ok":
            raise KubernetesRuntimeError("Kubernetes attester response is invalid")
        try:
            sandbox = _sandbox(response["sandbox"])
        except KubernetesRuntimeError, TypeError, ValueError:
            raise KubernetesRuntimeError(
                "Kubernetes attester response is invalid"
            ) from None
        unit = KubernetesRuntimeUnit(
            pod.unit_id,
            RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
            pod,
            sandbox,
        )
        previous = self._bound.setdefault(pod.pod_uid, unit)
        if not _same_runtime_identity(previous, unit):
            raise KubernetesRuntimeError("Kubernetes attester binding conflicts")
        return sandbox

    def confirm_exit(self, unit: KubernetesRuntimeUnit) -> EvidenceDigest:
        return self._proof("confirm_exit", unit, None)

    def recover_create_intent(
        self,
        pod: KubernetesPodIdentity,
        proposed_contract: KubernetesAttestationContract,
    ) -> tuple[KubernetesSandboxIdentity, KubernetesAttestationContract]:
        """Recover the attester-authenticated result of an uncommitted create."""

        if (
            type(pod) is not KubernetesPodIdentity
            or type(proposed_contract) is not KubernetesAttestationContract
        ):
            raise KubernetesRuntimeError("Kubernetes attester recovery is invalid")
        response = self._exchange(
            pod.node_name,
            {
                "operation": "recover_create_intent",
                "pod": _pod_mapping(pod),
                "proposed_contract": _contract_mapping(proposed_contract),
                "protocol": _PROTOCOL,
                "version": _VERSION,
            },
        )
        try:
            if (
                set(response) != {"contract", "outcome", "sandbox"}
                or response["outcome"] != "ok"
            ):
                raise ValueError
            contract = _contract(response["contract"])
            sandbox = _sandbox(response["sandbox"])
            unit = KubernetesRuntimeUnit(
                pod.unit_id,
                RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
                pod,
                sandbox,
            )
        except KeyError, TypeError, ValueError, KubernetesRuntimeError:
            raise KubernetesRuntimeError(
                "Kubernetes attester response is invalid"
            ) from None
        previous = self._bound.setdefault(pod.pod_uid, unit)
        if not _same_runtime_identity(previous, unit):
            raise KubernetesRuntimeError("Kubernetes attester binding conflicts")
        return sandbox, contract

    def adopt_create_intent(
        self,
        pod: KubernetesPodIdentity,
        contract: KubernetesAttestationContract,
    ) -> KubernetesSandboxIdentity:
        """Attest and durably bind an exact persisted create-intent Pod."""

        response = self._exchange(
            pod.node_name,
            {
                "operation": "adopt_create_intent",
                "pod": _pod_mapping(pod),
                "proposed_contract": _contract_mapping(contract),
                "protocol": _PROTOCOL,
                "version": _VERSION,
            },
        )
        try:
            if (
                set(response) != {"contract", "outcome", "sandbox"}
                or response["outcome"] != "ok"
                or _contract(response["contract"]) != contract
            ):
                raise ValueError
            sandbox = _sandbox(response["sandbox"])
            unit = KubernetesRuntimeUnit(
                pod.unit_id,
                RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
                pod,
                sandbox,
            )
        except KeyError, TypeError, ValueError, KubernetesRuntimeError:
            raise KubernetesRuntimeError(
                "Kubernetes attester response is invalid"
            ) from None
        self._bound[pod.pod_uid] = unit
        return sandbox

    def recover(
        self,
        unit: KubernetesRuntimeUnit,
        contract: KubernetesAttestationContract,
        lifecycle_state: ManagedUnitState,
    ) -> KubernetesSandboxIdentity:
        """Rebind a broker-recovered runtime unit to durable node state."""

        if (
            type(unit) is not KubernetesRuntimeUnit
            or type(contract) is not KubernetesAttestationContract
            or lifecycle_state
            not in {
                ManagedUnitState.CREATED,
                ManagedUnitState.EXIT_CONFIRMED,
                ManagedUnitState.EMPTY_CONFIRMED,
            }
        ):
            raise KubernetesRuntimeError("Kubernetes attester recovery is invalid")
        response = self._exchange(
            unit.pod.node_name,
            {
                "contract": _contract_mapping(contract),
                "lifecycle_state": lifecycle_state.value,
                "operation": "recover",
                "pod": _pod_mapping(unit.pod),
                "protocol": _PROTOCOL,
                "sandbox": _sandbox_mapping(unit.sandbox),
                "version": _VERSION,
            },
        )
        try:
            if set(response) != {"outcome", "sandbox"} or response["outcome"] != "ok":
                raise ValueError
            sandbox = _sandbox(response["sandbox"])
            if sandbox != unit.sandbox:
                raise ValueError
        except KeyError, TypeError, ValueError, KubernetesRuntimeError:
            raise KubernetesRuntimeError(
                "Kubernetes attester response is invalid"
            ) from None
        previous = self._bound.get(unit.pod.pod_uid)
        if previous is not None and not _same_runtime_identity(previous, unit):
            raise KubernetesRuntimeError("Kubernetes attester binding conflicts")
        self._bound[unit.pod.pod_uid] = unit
        return sandbox

    def acknowledge(
        self, unit: KubernetesRuntimeUnit, removal_evidence: EvidenceDigest
    ) -> None:
        """Release node lifecycle state after broker-durable proof ACK."""

        if (
            type(unit) is not KubernetesRuntimeUnit
            or type(removal_evidence) is not EvidenceDigest
        ):
            raise KubernetesRuntimeError("Kubernetes attester binding is unknown")
        response = self._exchange(
            unit.pod.node_name,
            {
                "operation": "acknowledge",
                "pod_uid": str(unit.pod.pod_uid),
                "protocol": _PROTOCOL,
                "removed_evidence": removal_evidence.value,
                "version": _VERSION,
            },
        )
        if response != {"acknowledged": True, "outcome": "ok"}:
            raise KubernetesRuntimeError("Kubernetes attester response is invalid")
        self._bound.pop(unit.pod.pod_uid, None)

    def confirm_empty(
        self, unit: KubernetesRuntimeUnit, exit_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        return self._proof("confirm_empty", unit, exit_evidence)

    def confirm_removed(
        self, unit: KubernetesRuntimeUnit, empty_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        return self._proof("confirm_removed", unit, empty_evidence)

    def _proof(
        self,
        operation: str,
        unit: KubernetesRuntimeUnit,
        prior: EvidenceDigest | None,
    ) -> EvidenceDigest:
        if type(unit) is not KubernetesRuntimeUnit or not _same_runtime_identity(
            self._bound.get(unit.pod.pod_uid), unit
        ):
            raise KubernetesRuntimeError("Kubernetes attester binding is unknown")
        if (operation == "confirm_exit") != (prior is None):
            raise KubernetesRuntimeError("Kubernetes attester proof request is invalid")
        request: dict[str, object] = {
            "operation": operation,
            "pod_uid": str(unit.pod.pod_uid),
            "protocol": _PROTOCOL,
            "version": _VERSION,
        }
        if prior is not None:
            request["prior_evidence"] = prior.value
        response = self._exchange_until_ready(
            unit.pod.node_name,
            request,
            timeout_message="Kubernetes attester proof readiness timed out",
        )
        if set(response) != {"evidence", "outcome"} or response["outcome"] != "ok":
            raise KubernetesRuntimeError("Kubernetes attester response is invalid")
        try:
            return EvidenceDigest(_required_text(response, "evidence"))
        except ValueError:
            raise KubernetesRuntimeError(
                "Kubernetes attester response is invalid"
            ) from None

    def _exchange_until_ready(
        self,
        node_name: str,
        request: Mapping[str, object],
        *,
        timeout_message: str,
    ) -> Mapping[str, object]:
        deadline = self._monotonic() + self._readiness.timeout_seconds
        while True:
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise KubernetesRuntimeError(timeout_message)
            response = self._exchange(
                node_name,
                request,
                timeout_seconds=min(self._limits.timeout_seconds, remaining),
            )
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise KubernetesRuntimeError(timeout_message)
            if response != {"outcome": "not_ready"}:
                return response
            self._sleep(min(self._readiness.poll_interval_seconds, remaining))

    def _exchange(
        self,
        node_name: str,
        request: Mapping[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> Mapping[str, object]:
        payload = _encode(request)
        if len(payload) > self._limits.max_request_bytes:
            raise KubernetesRuntimeError(
                "Kubernetes attester request exceeds its limit"
            )
        connection = http.client.HTTPSConnection(
            node_name,
            self._tls.port,
            timeout=(
                self._limits.timeout_seconds
                if timeout_seconds is None
                else timeout_seconds
            ),
            context=self._context,
        )
        try:
            connection.connect()
            socket = connection.sock
            if (
                socket is None
                or _certificate_digest(socket) != self._tls.expected_server_certificate
            ):
                raise KubernetesRuntimeError(
                    "Kubernetes attester server identity is invalid"
                )
            connection.request(
                "POST",
                _PATH,
                body=payload,
                headers={
                    "Content-Length": str(len(payload)),
                    "Content-Type": _CONTENT_TYPE,
                },
            )
            response = connection.getresponse()
            if response.status != _HTTP_OK:
                raise KubernetesRuntimeError("Kubernetes attester request failed")
            length = _content_length(response.getheader("Content-Length"))
            if (
                response.getheader("Content-Type") != _CONTENT_TYPE
                or length > self._limits.max_response_bytes
            ):
                raise KubernetesRuntimeError("Kubernetes attester request failed")
            body = response.read(length + 1)
            if len(body) != length:
                raise KubernetesRuntimeError("Kubernetes attester response is invalid")
            return _decode(body)
        except KubernetesRuntimeError:
            raise
        except Exception:
            raise KubernetesRuntimeError("Kubernetes attester request failed") from None
        finally:
            connection.close()


class AttesterHttpsServer:
    """Single-process bounded HTTPS server for a separately deployed attester."""

    def __init__(
        self,
        service: NodeAttesterService,
        tls: AttesterServerTlsConfig,
        limits: AttesterTransportLimits,
    ) -> None:
        if (
            type(service) is not NodeAttesterService
            or type(tls) is not AttesterServerTlsConfig
            or type(limits) is not AttesterTransportLimits
        ):
            raise ValueError("Kubernetes attester server is invalid")
        context = _server_context(tls)
        server = _AttesterHttpServer(
            (tls.listen_host, tls.port), service, tls, limits, context
        )
        self._server = server

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def serve_forever(self) -> None:
        self._server.serve_forever(poll_interval=0.25)

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _AttesterHttpServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = True

    def __init__(
        self,
        address: tuple[str, int],
        service: NodeAttesterService,
        tls: AttesterServerTlsConfig,
        limits: AttesterTransportLimits,
        context: ssl.SSLContext,
    ) -> None:
        self.attester_service = service
        self.tls_config = tls
        self.attester_limits = limits
        self.tls_context = context
        self.request_queue_size = limits.max_concurrent_requests
        self._admission = BoundedSemaphore(limits.max_concurrent_requests)
        super().__init__(address, _AttesterHandler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._admission.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._admission.release()
            raise

    def handle_error(self, request: Any, client_address: Any) -> None:
        del request, client_address

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        connection = None
        try:
            request.settimeout(self.attester_limits.timeout_seconds)
            connection = self.tls_context.wrap_socket(request, server_side=True)
            self.finish_request(connection, client_address)
        except Exception:
            self.handle_error(connection or request, client_address)
        finally:
            self.shutdown_request(connection or request)
            self._admission.release()


class _AttesterHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = ""
    sys_version = ""

    def do_POST(self) -> None:  # noqa: PLR0911
        server = cast(_AttesterHttpServer, self.server)
        self.connection.settimeout(server.attester_limits.timeout_seconds)
        if self.path != _PATH:
            self._empty(_HTTP_BAD_REQUEST)
            return
        if self.headers.get("Content-Type") != _CONTENT_TYPE:
            self._empty(_HTTP_UNSUPPORTED_MEDIA)
            return
        try:
            if (
                _certificate_digest(self.connection)
                != server.tls_config.expected_client_certificate
            ):
                self._empty(_HTTP_FORBIDDEN)
                return
        except Exception:
            self._empty(_HTTP_FORBIDDEN)
            return
        try:
            length = _content_length(self.headers.get("Content-Length"))
        except KubernetesRuntimeError:
            self._empty(_HTTP_BAD_REQUEST)
            return
        if length > server.attester_limits.max_request_bytes:
            self._empty(_HTTP_PAYLOAD_TOO_LARGE)
            return
        payload = self.rfile.read(length)
        if len(payload) != length:
            self._empty(_HTTP_BAD_REQUEST)
            return
        try:
            response = server.attester_service.handle(payload)
        except KubernetesRuntimeError:
            self._empty(_HTTP_BAD_REQUEST)
            return
        if len(response) > server.attester_limits.max_response_bytes:
            self._empty(_HTTP_PAYLOAD_TOO_LARGE)
            return
        self.send_response(_HTTP_OK)
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Content-Type", _CONTENT_TYPE)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)

    def do_GET(self) -> None:
        self._empty(_HTTP_METHOD_NOT_ALLOWED)

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()


def _client_context(config: AttesterClientTlsConfig) -> ssl.SSLContext:
    try:
        context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH, cafile=str(config.server_ca_file)
        )
        context.load_cert_chain(
            str(config.certificate_file), str(config.private_key_file)
        )
        _harden_context(context)
        context.check_hostname = True
        return context
    except OSError, ssl.SSLError, ValueError:
        raise KubernetesRuntimeError(
            "Kubernetes attester TLS configuration failed"
        ) from None


def _server_context(config: AttesterServerTlsConfig) -> ssl.SSLContext:
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_verify_locations(cafile=str(config.client_ca_file))
        context.load_cert_chain(
            str(config.certificate_file), str(config.private_key_file)
        )
        context.verify_mode = ssl.CERT_REQUIRED
        _harden_context(context)
        return context
    except OSError, ssl.SSLError, ValueError:
        raise KubernetesRuntimeError(
            "Kubernetes attester TLS configuration failed"
        ) from None


def _harden_context(context: ssl.SSLContext) -> None:
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.options |= ssl.OP_NO_COMPRESSION | ssl.OP_NO_TICKET
    context.verify_flags |= ssl.VERIFY_X509_STRICT


def _certificate_digest(socket: Any) -> EvidenceDigest:
    certificate = socket.getpeercert(binary_form=True)
    if type(certificate) is not bytes or not certificate:
        raise KubernetesRuntimeError("Kubernetes attester peer identity is invalid")
    return EvidenceDigest(f"sha256:{hashlib.sha256(certificate).hexdigest()}")


def _content_length(value: str | None) -> int:
    if (
        value is None
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
    ):
        raise KubernetesRuntimeError("Kubernetes attester content length is invalid")
    result = int(value)
    if result <= 0:
        raise KubernetesRuntimeError("Kubernetes attester content length is invalid")
    return result
