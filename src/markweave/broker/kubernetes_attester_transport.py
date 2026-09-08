"""Bounded mTLS client and server for the node-local Kubernetes attester."""

from __future__ import annotations

import hashlib
import http.client
import json
import ssl
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from markweave.broker.kubernetes_attester import NodeAttestationEngine
from markweave.broker.kubernetes_runtime import (
    KubernetesAttestationContract,
    KubernetesNodeAttester,
    KubernetesPodIdentity,
    KubernetesRuntimeError,
    KubernetesRuntimeUnit,
    KubernetesSandboxIdentity,
)
from markweave.broker.models import (
    BrokerPolicy,
    EvidenceDigest,
    RuntimeChannelLimits,
    RuntimeIncarnation,
    RuntimeLimits,
)

_PATH = "/v1/attest"
_CONTENT_TYPE = "application/json"
_PROTOCOL = "markweave-kubernetes-node-attester"
_VERSION = 1
_MAX_NODE_NAME_BYTES = 253
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

    def __post_init__(self) -> None:
        if (
            type(self.max_request_bytes) is not int
            or self.max_request_bytes <= 0
            or type(self.max_response_bytes) is not int
            or self.max_response_bytes <= 0
            or type(self.timeout_seconds) not in {int, float}
            or self.timeout_seconds <= 0
        ):
            raise ValueError("Kubernetes attester transport limits are invalid")


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


class NodeAttesterService:
    """Authorize a closed operation set around one node-local policy engine."""

    def __init__(self, engine: NodeAttestationEngine, *, node_name: str) -> None:
        if (
            type(engine) is not NodeAttestationEngine
            or type(node_name) is not str
            or not node_name
            or len(node_name.encode("utf-8")) > _MAX_NODE_NAME_BYTES
        ):
            raise ValueError("Kubernetes node attester service is invalid")
        self._engine = engine
        self._node_name = node_name
        self._bound: dict[UUID, KubernetesRuntimeUnit] = {}
        self._exit_evidence: dict[UUID, EvidenceDigest] = {}
        self._empty_evidence: dict[UUID, EvidenceDigest] = {}

    def handle(self, payload: bytes) -> bytes:
        """Validate one content-free request and return a canonical response."""

        try:
            request = _decode(payload)
            if (
                request.get("protocol") != _PROTOCOL
                or request.get("version") != _VERSION
            ):
                raise ValueError
            operation = request.get("operation")
            if operation == "bind":
                response = self._bind(request)
            elif operation in {"confirm_exit", "confirm_empty", "confirm_removed"}:
                response = self._proof(cast(str, operation), request)
            else:
                raise ValueError
            return _encode({"outcome": "ok", **response})
        except KubernetesRuntimeError:
            raise
        except Exception:
            raise KubernetesRuntimeError(
                "Kubernetes attester request is invalid"
            ) from None

    def _bind(self, request: Mapping[str, object]) -> dict[str, object]:
        if set(request) != {"contract", "operation", "pod", "protocol", "version"}:
            raise ValueError
        pod = _pod(request["pod"])
        if pod.node_name != self._node_name:
            raise KubernetesRuntimeError("Kubernetes attester node binding is invalid")
        contract = _contract(request["contract"])
        sandbox = self._engine.bind(pod, contract)
        unit = KubernetesRuntimeUnit(
            pod.unit_id,
            RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
            pod,
            sandbox,
        )
        previous = self._bound.setdefault(pod.pod_uid, unit)
        if previous != unit:
            raise KubernetesRuntimeError("Kubernetes attester binding conflicts")
        return {"sandbox": _sandbox_mapping(sandbox)}

    def _proof(
        self, operation: str, request: Mapping[str, object]
    ) -> dict[str, object]:
        expected = {"operation", "pod_uid", "protocol", "version"}
        if operation != "confirm_exit":
            expected.add("prior_evidence")
        if set(request) != expected:
            raise ValueError
        pod_uid = UUID(_required_text(request, "pod_uid"))
        unit = self._bound.get(pod_uid)
        if unit is None:
            raise KubernetesRuntimeError("Kubernetes attester binding is unknown")
        if operation == "confirm_exit":
            evidence = self._engine.confirm_exit(unit)
        elif operation == "confirm_empty":
            prior = EvidenceDigest(_required_text(request, "prior_evidence"))
            if self._exit_evidence.get(pod_uid) != prior:
                raise KubernetesRuntimeError(
                    "Kubernetes attester exit evidence is invalid"
                )
            evidence = self._engine.confirm_empty(unit, prior)
        else:
            prior = EvidenceDigest(_required_text(request, "prior_evidence"))
            if self._empty_evidence.get(pod_uid) != prior:
                raise KubernetesRuntimeError(
                    "Kubernetes attester empty evidence is invalid"
                )
            evidence = self._engine.confirm_removed(unit, prior)
            self._bound.pop(pod_uid, None)
            self._exit_evidence.pop(pod_uid, None)
            self._empty_evidence.pop(pod_uid, None)
        if operation == "confirm_exit":
            previous = self._exit_evidence.setdefault(pod_uid, evidence)
            if previous != evidence:
                raise KubernetesRuntimeError(
                    "Kubernetes attester exit evidence conflicts"
                )
        elif operation == "confirm_empty":
            previous = self._empty_evidence.setdefault(pod_uid, evidence)
            if previous != evidence:
                raise KubernetesRuntimeError(
                    "Kubernetes attester empty evidence conflicts"
                )
        return {"evidence": evidence.value}


class HttpsNodeAttesterClient(KubernetesNodeAttester):
    """Route each request only to the node named by the scheduled Pod."""

    def __init__(
        self, tls: AttesterClientTlsConfig, limits: AttesterTransportLimits
    ) -> None:
        if (
            type(tls) is not AttesterClientTlsConfig
            or type(limits) is not AttesterTransportLimits
        ):
            raise ValueError("Kubernetes attester client is invalid")
        self._tls = tls
        self._limits = limits
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
        response = self._exchange(pod.node_name, request)
        if set(response) != {"outcome", "sandbox"} or response["outcome"] != "ok":
            raise KubernetesRuntimeError("Kubernetes attester response is invalid")
        sandbox = _sandbox(response["sandbox"])
        unit = KubernetesRuntimeUnit(
            pod.unit_id,
            RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
            pod,
            sandbox,
        )
        previous = self._bound.setdefault(pod.pod_uid, unit)
        if previous != unit:
            raise KubernetesRuntimeError("Kubernetes attester binding conflicts")
        return sandbox

    def confirm_exit(self, unit: KubernetesRuntimeUnit) -> EvidenceDigest:
        return self._proof("confirm_exit", unit, None)

    def confirm_empty(
        self, unit: KubernetesRuntimeUnit, exit_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        return self._proof("confirm_empty", unit, exit_evidence)

    def confirm_removed(
        self, unit: KubernetesRuntimeUnit, empty_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        evidence = self._proof("confirm_removed", unit, empty_evidence)
        self._bound.pop(unit.pod.pod_uid, None)
        return evidence

    def _proof(
        self,
        operation: str,
        unit: KubernetesRuntimeUnit,
        prior: EvidenceDigest | None,
    ) -> EvidenceDigest:
        if (
            type(unit) is not KubernetesRuntimeUnit
            or self._bound.get(unit.pod.pod_uid) != unit
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
        response = self._exchange(unit.pod.node_name, request)
        if set(response) != {"evidence", "outcome"} or response["outcome"] != "ok":
            raise KubernetesRuntimeError("Kubernetes attester response is invalid")
        try:
            return EvidenceDigest(_required_text(response, "evidence"))
        except ValueError:
            raise KubernetesRuntimeError(
                "Kubernetes attester response is invalid"
            ) from None

    def _exchange(
        self, node_name: str, request: Mapping[str, object]
    ) -> Mapping[str, object]:
        payload = _encode(request)
        if len(payload) > self._limits.max_request_bytes:
            raise KubernetesRuntimeError(
                "Kubernetes attester request exceeds its limit"
            )
        connection = http.client.HTTPSConnection(
            node_name,
            self._tls.port,
            timeout=self._limits.timeout_seconds,
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
            length = _content_length(response.getheader("Content-Length"))
            if (
                response.status != _HTTP_OK
                or response.getheader("Content-Type") != _CONTENT_TYPE
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
        server = _AttesterHttpServer((tls.listen_host, tls.port), service, tls, limits)
        context = _server_context(tls)
        server.socket = context.wrap_socket(server.socket, server_side=True)
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


class _AttesterHttpServer(HTTPServer):
    allow_reuse_address = False

    def __init__(
        self,
        address: tuple[str, int],
        service: NodeAttesterService,
        tls: AttesterServerTlsConfig,
        limits: AttesterTransportLimits,
    ) -> None:
        self.attester_service = service
        self.tls_config = tls
        self.attester_limits = limits
        super().__init__(address, _AttesterHandler)


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


def _encode(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except TypeError, ValueError, UnicodeError:
        raise KubernetesRuntimeError("Kubernetes attester message is invalid") from None


def _decode(value: bytes) -> Mapping[str, object]:
    if type(value) is not bytes:
        raise KubernetesRuntimeError("Kubernetes attester message is invalid")
    try:
        result = json.loads(value.decode("ascii"))
    except UnicodeError, json.JSONDecodeError:
        raise KubernetesRuntimeError("Kubernetes attester message is invalid") from None
    if not isinstance(result, Mapping) or any(type(key) is not str for key in result):
        raise KubernetesRuntimeError("Kubernetes attester message is invalid")
    return cast(Mapping[str, object], result)


def _pod_mapping(value: KubernetesPodIdentity) -> dict[str, object]:
    return {
        "attempt_id": str(value.attempt_id),
        "name": value.name,
        "namespace": value.namespace,
        "node_name": value.node_name,
        "pod_uid": str(value.pod_uid),
        "policy_revision": value.policy_revision,
        "policy_specification": value.policy_specification.value,
        "principal_id": str(value.principal_id),
        "unit_id": str(value.unit_id),
    }


def _pod(value: object) -> KubernetesPodIdentity:
    mapping = _closed_mapping(
        value,
        {
            "attempt_id",
            "name",
            "namespace",
            "node_name",
            "pod_uid",
            "policy_revision",
            "policy_specification",
            "principal_id",
            "unit_id",
        },
    )
    return KubernetesPodIdentity(
        _required_text(mapping, "namespace"),
        _required_text(mapping, "name"),
        UUID(_required_text(mapping, "pod_uid")),
        _required_text(mapping, "node_name"),
        UUID(_required_text(mapping, "unit_id")),
        UUID(_required_text(mapping, "attempt_id")),
        UUID(_required_text(mapping, "principal_id")),
        _required_text(mapping, "policy_revision"),
        EvidenceDigest(_required_text(mapping, "policy_specification")),
    )


def _sandbox_mapping(value: KubernetesSandboxIdentity) -> dict[str, object]:
    return {
        "cgroup_path": value.cgroup_path,
        "node_fence": value.node_fence.value,
        "node_uid": str(value.node_uid),
        "sandbox_id": value.sandbox_id,
    }


def _sandbox(value: object) -> KubernetesSandboxIdentity:
    mapping = _closed_mapping(
        value, {"cgroup_path", "node_fence", "node_uid", "sandbox_id"}
    )
    return KubernetesSandboxIdentity(
        _required_text(mapping, "sandbox_id"),
        _required_text(mapping, "cgroup_path"),
        UUID(_required_text(mapping, "node_uid")),
        EvidenceDigest(_required_text(mapping, "node_fence")),
    )


def _contract_mapping(value: KubernetesAttestationContract) -> dict[str, object]:
    return {
        "fence_revision": value.fence_revision,
        "manifest_digest": value.manifest_digest.value,
        "pod_contract": dict(value.pod_contract),
        "policy": asdict(value.policy),
        "pool": value.pool,
    }


def _contract(value: object) -> KubernetesAttestationContract:
    mapping = _closed_mapping(
        value,
        {"fence_revision", "manifest_digest", "pod_contract", "policy", "pool"},
    )
    policy = _closed_mapping(
        mapping["policy"],
        {"channel_limits", "image_digest", "limits", "revision"},
    )
    limits = _closed_mapping(
        policy["limits"],
        {
            "cpu_period_micros",
            "cpu_quota_micros",
            "memory_bytes",
            "pid_limit",
            "wall_time_millis",
            "workspace_bytes",
        },
    )
    channel = _closed_mapping(
        policy["channel_limits"], {"max_input_bytes", "max_output_bytes"}
    )
    pod_contract = mapping["pod_contract"]
    if not isinstance(pod_contract, Mapping):
        raise ValueError
    return KubernetesAttestationContract(
        cast(Mapping[str, object], pod_contract),
        EvidenceDigest(_required_text(mapping, "manifest_digest")),
        BrokerPolicy(
            _required_text(policy, "revision"),
            _required_text(policy, "image_digest"),
            RuntimeLimits(**_integers(limits)),
            RuntimeChannelLimits(**_integers(channel)),
        ),
        _required_text(mapping, "pool"),
        _required_text(mapping, "fence_revision"),
    )


def _integers(value: Mapping[str, object]) -> dict[str, int]:
    if any(type(item) is not int for item in value.values()):
        raise ValueError
    return cast(dict[str, int], dict(value))


def _closed_mapping(value: object, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError
    return cast(Mapping[str, object], value)


def _required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if type(item) is not str or not item:
        raise ValueError
    return item


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
