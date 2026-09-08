"""Concrete namespace-scoped Kubernetes Pod and exec adapter.

The adapter deliberately exposes only the operations required by
``KubernetesControlPlane``.  It never accepts a namespace, container, command,
or path from a broker caller; those values are fixed by its configuration.
"""

from __future__ import annotations

import base64
import functools
import json
import ssl
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID

from kubernetes import client
from kubernetes import config as kubernetes_config
from kubernetes.client.exceptions import ApiException
from kubernetes.stream import ws_client
from kubernetes.stream.stream import _websocket_request

from markweave.broker.kubernetes_runtime import (
    KubernetesPodIdentity,
    KubernetesRuntimeError,
)
from markweave.broker.models import EvidenceDigest, RuntimeChannelLimits
from markweave.reversions.attempt_channel import (
    MAX_METADATA_BYTES,
    decode_channel_state,
    decode_response_metadata,
    encode_channel_state,
    encode_request_metadata,
)
from markweave.reversions.errors import ReverseConversionError
from markweave.reversions.models import ReverseAttemptRequest, ReverseAttemptResponse

_MANAGED_LABEL = "reverse.markweave.dev/managed"
_UNIT_LABEL = "reverse.markweave.dev/unit-id"
_ATTEMPT_LABEL = "reverse.markweave.dev/attempt-id"
_PRINCIPAL_LABEL = "reverse.markweave.dev/principal-id"
_POLICY_LABEL = "reverse.markweave.dev/policy-revision"
_SPECIFICATION_ANNOTATION = "reverse.markweave.dev/policy-specification"
_CONTAINER = "attempt"
_MAX_API_OBJECT_BYTES = 256 * 1024
_MAX_EXEC_ERROR_BYTES = 4096
_NOT_FOUND = 404
_STAGE_SCRIPT = """import base64,json,os,sys
if os.environ.get('MARKWEAVE_POD_UID')!=sys.argv[1]: raise SystemExit(3)
p=json.loads(sys.stdin.buffer.read())
if set(p)!={'request','source','state','commit'}: raise SystemExit(2)
for n,k in (('request.json','request'),('source.bin','source'),('response.state','state'),('request.commit','commit')):
 d=base64.b64decode(p[k],validate=True); q='/work/.'+n+'.tmp'
 try: os.unlink(q)
 except FileNotFoundError: pass
 f=os.open(q,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o440)
 try:
  with os.fdopen(f,'wb') as h: h.write(d); h.flush(); os.fsync(h.fileno())
 except BaseException:
  try: os.close(f)
  except OSError: pass
  raise
 os.replace(q,'/work/'+n)
"""
_READ_SCRIPT = """import base64,pathlib,sys
import os
if os.environ.get('MARKWEAVE_POD_UID')!=sys.argv[1]: raise SystemExit(3)
n=sys.argv[2]; m=int(sys.argv[3]); p=pathlib.Path('/work')/n
d=p.read_bytes()
if len(d)>m: raise SystemExit(2)
sys.stdout.write(base64.b64encode(d).decode('ascii'))
"""
_KILL_SCRIPT = """import os,signal,sys
if os.environ.get('MARKWEAVE_POD_UID')!=sys.argv[1]: raise SystemExit(3)
os.kill(1,signal.SIGKILL)
"""


class CoreV1ApiPort(Protocol):
    """Generated-client surface used by the namespace adapter."""

    api_client: Any

    def create_namespaced_pod(
        self, namespace: str, body: object, **kwargs: object
    ) -> Any: ...

    def read_namespaced_pod(
        self, name: str, namespace: str, **kwargs: object
    ) -> Any: ...

    def list_namespaced_pod(self, namespace: str, **kwargs: object) -> Any: ...

    def delete_namespaced_pod(
        self, name: str, namespace: str, **kwargs: object
    ) -> Any: ...

    def connect_get_namespaced_pod_exec(
        self, name: str, namespace: str, **kwargs: object
    ) -> Any: ...


class ExecSession(Protocol):
    """Bounded synchronous Kubernetes exec stream."""

    def write_stdin(self, data: str) -> None: ...

    def close_stdin(self) -> None: ...

    def is_open(self) -> bool: ...

    def update(self, timeout: float = 0) -> None: ...

    def peek_stdout(self) -> bool: ...

    def read_stdout(self, timeout: float | None = None) -> str: ...

    def peek_stderr(self) -> bool: ...

    def read_stderr(self, timeout: float | None = None) -> str: ...

    @property
    def returncode(self) -> int | None: ...

    def close(self, **kwargs: object) -> None: ...


ExecFactory = Callable[..., ExecSession]


def _bounded_create_websocket(
    configuration: Any,
    url: str,
    headers: Mapping[str, str] | None,
    timeout_seconds: float,
) -> Any:
    header: list[str] = []
    if headers and "authorization" in headers:
        header.append(f"authorization: {headers['authorization']}")
    if headers and "sec-websocket-protocol" in headers:
        header.append(f"sec-websocket-protocol: {headers['sec-websocket-protocol']}")
    else:
        header.append("sec-websocket-protocol: v4.channel.k8s.io")
    if url.startswith("wss://") and configuration.verify_ssl:
        ssl_options: dict[str, object] = {
            "cert_reqs": ssl.CERT_REQUIRED,
            "ca_certs": configuration.ssl_ca_cert or ws_client.certifi.where(),
        }
        if configuration.assert_hostname is not None:
            ssl_options["check_hostname"] = configuration.assert_hostname
    else:
        ssl_options = {"cert_reqs": ssl.CERT_NONE}
    if configuration.cert_file:
        ssl_options["certfile"] = configuration.cert_file
    if configuration.key_file:
        ssl_options["keyfile"] = configuration.key_file
    if configuration.tls_server_name:
        ssl_options["server_hostname"] = configuration.tls_server_name
    websocket = ws_client.WebSocket(sslopt=ssl_options, skip_utf8_validation=False)
    connect_options: dict[str, object] = {
        "header": header,
        "timeout": timeout_seconds,
    }
    if configuration.proxy or configuration.proxy_headers:
        connect_options = ws_client.websocket_proxycare(
            connect_options, configuration, url, headers
        )
    websocket.connect(url, **connect_options)
    return websocket


class _BoundedWsClient(ws_client.WSClient):
    def __init__(  # noqa: PLR0913 - mirrors pinned Kubernetes WSClient contract
        self,
        configuration: Any,
        url: str,
        headers: Mapping[str, str] | None,
        capture_all: bool,
        *,
        binary: bool,
        timeout_seconds: float,
    ) -> None:
        self._connected = False
        self._channels = {}
        self.binary = binary
        self.newline = b"\n" if binary else "\n"
        if capture_all:
            self._all = ws_client.BytesIO() if binary else ws_client.StringIO()
        else:
            self._all = ws_client._IgnoredIO()
        self.sock = _bounded_create_websocket(
            configuration, url, headers, timeout_seconds
        )
        self._connected = True
        self._returncode = None


def _bounded_websocket_call(
    configuration: Any, method: str, url: str, **kwargs: object
) -> object:
    del method
    try:
        request_timeout: object = kwargs.get("_request_timeout", 60)
        timeout_value: object = (
            request_timeout[0]
            if isinstance(request_timeout, tuple) and request_timeout
            else request_timeout
        )
        if type(timeout_value) not in {int, float}:
            raise ValueError
        timeout_seconds = float(cast(int | float, timeout_value))
        if timeout_seconds <= 0:
            raise ValueError
        websocket_url = ws_client.get_websocket_url(url, kwargs.get("query_params"))
        client = _BoundedWsClient(
            configuration,
            websocket_url,
            cast(Mapping[str, str] | None, kwargs.get("headers")),
            bool(kwargs.get("capture_all", True)),
            binary=bool(kwargs.get("binary", False)),
            timeout_seconds=timeout_seconds,
        )
        if not bool(kwargs.get("_preload_content", True)):
            return client
        client.run_forever(timeout=timeout_seconds)
        return ws_client.WSResponse(client.read_all())
    except (Exception, KeyboardInterrupt, SystemExit) as error:
        raise ApiException(status=0, reason=str(error)) from None


_BOUNDED_STREAM: ExecFactory = functools.partial(
    _websocket_request, _bounded_websocket_call, None
)


@dataclass(frozen=True, slots=True)
class KubernetesApiConfig:
    """Broker-owned namespace and bounded API timing policy."""

    namespace: str
    scheduling_timeout_seconds: float
    exec_timeout_seconds: float
    poll_interval_seconds: float
    channel_limits: RuntimeChannelLimits

    def __post_init__(self) -> None:
        if (
            type(self.namespace) is not str
            or not self.namespace
            or any(
                type(value) not in {int, float} or value <= 0
                for value in (
                    self.scheduling_timeout_seconds,
                    self.exec_timeout_seconds,
                    self.poll_interval_seconds,
                )
            )
            or self.poll_interval_seconds > self.scheduling_timeout_seconds
            or type(self.channel_limits) is not RuntimeChannelLimits
        ):
            raise ValueError("Kubernetes API configuration is invalid")


class KubernetesApiControlPlane:
    """Use one namespaced CoreV1 API identity and fixed exec commands."""

    def __init__(
        self,
        api: CoreV1ApiPort,
        config: KubernetesApiConfig,
        *,
        exec_factory: ExecFactory = _BOUNDED_STREAM,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not _implements_api(api) or type(config) is not KubernetesApiConfig:
            raise ValueError("Kubernetes API adapter is invalid")
        self._api = api
        self._config = config
        self._exec_factory = exec_factory
        self._monotonic = monotonic
        self._sleep = sleep

    @classmethod
    def in_cluster(cls, config: KubernetesApiConfig) -> KubernetesApiControlPlane:
        """Build an adapter from the broker Pod's namespace-scoped identity."""

        try:
            kubernetes_config.load_incluster_config()
            api = client.CoreV1Api()
        except Exception:
            raise KubernetesRuntimeError(
                "Kubernetes API initialization failed"
            ) from None
        return cls(api, config)

    def create(self, manifest: Mapping[str, object]) -> KubernetesPodIdentity:
        if _manifest_namespace(manifest) != self._config.namespace:
            raise KubernetesRuntimeError("Kubernetes Pod create request is invalid")
        try:
            created = self._api.create_namespaced_pod(
                self._config.namespace,
                dict(manifest),
                _request_timeout=self._config.scheduling_timeout_seconds,
            )
            deadline = self._monotonic() + self._config.scheduling_timeout_seconds
            current = created
            name = self._pod_name(current)
            while not self._node_assigned(current):
                if self._monotonic() >= deadline:
                    raise KubernetesRuntimeError("Kubernetes Pod scheduling timed out")
                self._sleep(self._config.poll_interval_seconds)
                current = self._api.read_namespaced_pod(
                    name,
                    self._config.namespace,
                    _request_timeout=self._config.scheduling_timeout_seconds,
                )
            return self._identity(current)
        except KubernetesRuntimeError:
            raise
        except Exception:
            raise KubernetesRuntimeError("Kubernetes Pod creation failed") from None

    def find(self, name: str) -> KubernetesPodIdentity | None:
        """Read one deterministic broker-authored Pod name without label discovery."""

        if type(name) is not str or not name:
            raise KubernetesRuntimeError("Kubernetes Pod lookup is invalid")
        try:
            current = self._api.read_namespaced_pod(
                name,
                self._config.namespace,
                _request_timeout=self._config.scheduling_timeout_seconds,
            )
            deadline = self._monotonic() + self._config.scheduling_timeout_seconds
            while not self._node_assigned(current):
                if self._monotonic() >= deadline:
                    raise KubernetesRuntimeError("Kubernetes Pod scheduling timed out")
                self._sleep(self._config.poll_interval_seconds)
                current = self._api.read_namespaced_pod(
                    name,
                    self._config.namespace,
                    _request_timeout=self._config.scheduling_timeout_seconds,
                )
        except ApiException as error:
            if error.status == _NOT_FOUND:
                return None
            raise KubernetesRuntimeError("Kubernetes Pod lookup failed") from None
        except Exception:
            raise KubernetesRuntimeError("Kubernetes Pod lookup failed") from None
        return self._identity(current)

    def stage_request(
        self, pod: KubernetesPodIdentity, request: ReverseAttemptRequest
    ) -> None:
        if (
            type(request) is not ReverseAttemptRequest
            or request.attempt_id != pod.attempt_id
        ):
            raise KubernetesRuntimeError("Kubernetes workspace request is invalid")
        if (
            len(request.source) > self._config.channel_limits.max_input_bytes
            or request.limits.max_input_bytes
            > self._config.channel_limits.max_input_bytes
            or request.limits.max_output_bytes
            > self._config.channel_limits.max_output_bytes
        ):
            raise KubernetesRuntimeError("Kubernetes workspace request is invalid")
        self._require_current(pod)
        payload = json.dumps(
            {
                "commit": _b64(b"committed\n"),
                "request": _b64(encode_request_metadata(request)),
                "source": _b64(request.source),
                "state": _b64(encode_channel_state(request.attempt_id, "pending")),
            },
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        self._exec(pod, ("python", "-c", _STAGE_SCRIPT, str(pod.pod_uid)), payload)

    def try_collect_response(
        self, pod: KubernetesPodIdentity, expected_attempt_id: UUID
    ) -> ReverseAttemptResponse | None:
        if (
            type(expected_attempt_id) is not UUID
            or expected_attempt_id != pod.attempt_id
        ):
            raise KubernetesRuntimeError("Kubernetes workspace response is invalid")
        self._require_current(pod)
        try:
            state = self._read_file(pod, "response.state", MAX_METADATA_BYTES)
            if decode_channel_state(state, expected_attempt_id) == "pending":
                return None
            metadata = self._read_file(pod, "response.json", MAX_METADATA_BYTES)
            shape = json.loads(metadata.decode("ascii"))
            result = None
            if isinstance(shape, Mapping) and shape.get("type") == "success":
                result = self._read_file(
                    pod, "result.bin", self._config.channel_limits.max_output_bytes
                )
            response = decode_response_metadata(metadata, result)
        except UnicodeDecodeError, json.JSONDecodeError, ReverseConversionError:
            raise KubernetesRuntimeError(
                "Kubernetes workspace response is invalid"
            ) from None
        if response.attempt_id != expected_attempt_id:
            raise KubernetesRuntimeError("Kubernetes workspace response is invalid")
        return response

    def terminate(self, pod: KubernetesPodIdentity) -> None:
        self._require_current(pod)
        # The connection commonly closes when PID 1 dies.  CRI evidence, not the
        # exec acknowledgement, proves the subsequent exit.
        with suppress(KubernetesRuntimeError):
            self._exec(pod, ("python", "-c", _KILL_SCRIPT, str(pod.pod_uid)), "")

    def delete(self, pod: KubernetesPodIdentity) -> None:
        if not self._current_or_absent(pod):
            return
        body = client.V1DeleteOptions(
            grace_period_seconds=0,
            preconditions=client.V1Preconditions(uid=str(pod.pod_uid)),
            propagation_policy="Background",
        )
        try:
            self._api.delete_namespaced_pod(
                pod.name,
                self._config.namespace,
                body=body,
                _request_timeout=self._config.scheduling_timeout_seconds,
            )
        except ApiException as error:
            if error.status == _NOT_FOUND:
                return
            raise KubernetesRuntimeError("Kubernetes Pod deletion failed") from None
        except Exception:
            raise KubernetesRuntimeError("Kubernetes Pod deletion failed") from None
        deadline = self._monotonic() + self._config.scheduling_timeout_seconds
        while not self.absent(pod):
            if self._monotonic() >= deadline:
                raise KubernetesRuntimeError("Kubernetes Pod deletion timed out")
            self._sleep(self._config.poll_interval_seconds)

    def absent(self, pod: KubernetesPodIdentity) -> bool:
        try:
            current = self._api.read_namespaced_pod(
                pod.name,
                self._config.namespace,
                _request_timeout=self._config.scheduling_timeout_seconds,
            )
        except ApiException as error:
            if error.status == _NOT_FOUND:
                return True
            raise KubernetesRuntimeError(
                "Kubernetes Pod absence lookup failed"
            ) from None
        except Exception:
            raise KubernetesRuntimeError(
                "Kubernetes Pod absence lookup failed"
            ) from None
        if self._identity(current) != pod:
            raise KubernetesRuntimeError("Kubernetes Pod identity changed")
        return False

    def discover(
        self, *, namespace: str, labels: Mapping[str, str], limit: int
    ) -> tuple[KubernetesPodIdentity, ...]:
        if (
            namespace != self._config.namespace
            or labels != {_MANAGED_LABEL: "1"}
            or type(limit) is not int
            or limit <= 0
        ):
            raise KubernetesRuntimeError("Kubernetes discovery request is invalid")
        try:
            result = self._api.list_namespaced_pod(
                self._config.namespace,
                label_selector=f"{_MANAGED_LABEL}=1",
                limit=limit + 1,
                _request_timeout=self._config.scheduling_timeout_seconds,
            )
            items = getattr(result, "items", None)
            metadata = getattr(result, "metadata", None)
            continuation = getattr(metadata, "_continue", None)
            remaining = getattr(metadata, "remaining_item_count", None)
            if (
                type(items) is not list
                or len(items) > limit
                or continuation not in {None, ""}
                or remaining not in {None, 0}
            ):
                raise KubernetesRuntimeError("Kubernetes discovery exceeds its limit")
            return tuple(self._identity(item) for item in items)
        except KubernetesRuntimeError:
            raise
        except Exception:
            raise KubernetesRuntimeError("Kubernetes discovery failed") from None

    def _require_current(self, expected: KubernetesPodIdentity) -> None:
        if not self._current_or_absent(expected):
            raise KubernetesRuntimeError("Kubernetes Pod identity lookup failed")

    def _current_or_absent(self, expected: KubernetesPodIdentity) -> bool:
        try:
            value = self._api.read_namespaced_pod(
                expected.name,
                self._config.namespace,
                _request_timeout=self._config.scheduling_timeout_seconds,
            )
        except ApiException as error:
            if error.status == _NOT_FOUND:
                return False
            raise KubernetesRuntimeError(
                "Kubernetes Pod identity lookup failed"
            ) from None
        except Exception:
            raise KubernetesRuntimeError(
                "Kubernetes Pod identity lookup failed"
            ) from None
        if self._identity(value) != expected:
            raise KubernetesRuntimeError("Kubernetes Pod identity changed")
        return True

    def _identity(self, value: object) -> KubernetesPodIdentity:
        try:
            serialized = self._serialized(value)
            encoded = json.dumps(serialized, allow_nan=False).encode("utf-8")
            if len(encoded) > _MAX_API_OBJECT_BYTES:
                raise ValueError
            metadata = _mapping(serialized, "metadata")
            specification = _mapping(serialized, "spec")
            labels = _mapping(metadata, "labels")
            annotations = _mapping(metadata, "annotations")
            namespace = _text(metadata, "namespace")
            if namespace != self._config.namespace:
                raise ValueError
            return KubernetesPodIdentity(
                namespace,
                _text(metadata, "name"),
                UUID(_text(metadata, "uid")),
                _text(specification, "nodeName"),
                UUID(_text(labels, _UNIT_LABEL)),
                UUID(_text(labels, _ATTEMPT_LABEL)),
                UUID(_text(labels, _PRINCIPAL_LABEL)),
                _text(labels, _POLICY_LABEL),
                _evidence(_text(annotations, _SPECIFICATION_ANNOTATION)),
            )
        except KeyError, TypeError, ValueError, UnicodeError:
            raise KubernetesRuntimeError("Kubernetes Pod identity is invalid") from None

    def _pod_name(self, value: object) -> str:
        try:
            return _text(_mapping(self._serialized(value), "metadata"), "name")
        except KeyError, TypeError, ValueError:
            raise KubernetesRuntimeError("Kubernetes Pod identity is invalid") from None

    def _node_assigned(self, value: object) -> bool:
        try:
            specification = _mapping(self._serialized(value), "spec")
            node_name = specification.get("nodeName")
            return type(node_name) is str and bool(node_name)
        except KeyError, TypeError:
            raise KubernetesRuntimeError("Kubernetes Pod identity is invalid") from None

    def _serialized(self, value: object) -> Mapping[str, object]:
        serialized = self._api.api_client.sanitize_for_serialization(value)
        if not isinstance(serialized, Mapping):
            raise TypeError
        return cast(Mapping[str, object], serialized)

    def _read_file(self, pod: KubernetesPodIdentity, leaf: str, maximum: int) -> bytes:
        if leaf not in {"response.state", "response.json", "result.bin"}:
            raise KubernetesRuntimeError("Kubernetes workspace path is invalid")
        encoded = self._exec(
            pod,
            ("python", "-c", _READ_SCRIPT, str(pod.pod_uid), leaf, str(maximum)),
            "",
            max_stdout=((maximum + 2) // 3) * 4,
        )
        try:
            result = base64.b64decode(encoded, validate=True)
        except ValueError, UnicodeEncodeError:
            raise KubernetesRuntimeError(
                "Kubernetes workspace response is invalid"
            ) from None
        if len(result) > maximum:
            raise KubernetesRuntimeError("Kubernetes workspace response is invalid")
        return result

    def _exec(
        self,
        pod: KubernetesPodIdentity,
        command: tuple[str, ...],
        stdin: str,
        *,
        max_stdout: int = MAX_METADATA_BYTES * 2,
    ) -> str:
        session: ExecSession | None = None
        deadline = self._monotonic() + self._config.exec_timeout_seconds
        try:
            session = self._exec_factory(
                self._api.connect_get_namespaced_pod_exec,
                pod.name,
                self._config.namespace,
                command=list(command),
                container=_CONTAINER,
                stderr=True,
                stdin=True,
                stdout=True,
                tty=False,
                _preload_content=False,
                _request_timeout=self._config.exec_timeout_seconds,
            )
            # The fixed helper is now blocked on stdin. Re-read the API identity
            # before releasing any data or lifecycle action into that channel.
            self._require_current(pod)
            if stdin:
                session.write_stdin(stdin)
            session.close_stdin()
            stdout: list[str] = []
            stdout_bytes = 0
            stderr_bytes = 0
            while session.is_open():
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise KubernetesRuntimeError("Kubernetes exec timed out")
                session.update(
                    timeout=min(remaining, self._config.poll_interval_seconds)
                )
                while session.peek_stdout():
                    chunk = session.read_stdout(timeout=0)
                    stdout_bytes += len(chunk.encode("utf-8"))
                    if stdout_bytes > max_stdout:
                        raise KubernetesRuntimeError(
                            "Kubernetes exec output exceeds its limit"
                        )
                    stdout.append(chunk)
                while session.peek_stderr():
                    chunk = session.read_stderr(timeout=0)
                    stderr_bytes += len(chunk.encode("utf-8"))
                    if stderr_bytes > _MAX_EXEC_ERROR_BYTES:
                        raise KubernetesRuntimeError(
                            "Kubernetes exec error exceeds its limit"
                        )
            if session.returncode != 0:
                raise KubernetesRuntimeError("Kubernetes exec failed")
            return "".join(stdout)
        except KubernetesRuntimeError:
            raise
        except Exception:
            raise KubernetesRuntimeError("Kubernetes exec failed") from None
        finally:
            if session is not None:
                with suppress(Exception):
                    session.close()


def _manifest_namespace(manifest: Mapping[str, object]) -> str | None:
    metadata = manifest.get("metadata")
    return (
        cast(str | None, metadata.get("namespace"))
        if isinstance(metadata, Mapping)
        else None
    )


def _implements_api(value: object) -> bool:
    return hasattr(value, "api_client") and all(
        callable(getattr(value, name, None))
        for name in (
            "create_namespaced_pod",
            "read_namespaced_pod",
            "list_namespaced_pod",
            "delete_namespaced_pod",
            "connect_get_namespaced_pod_exec",
        )
    )


def _mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    result = value[key]
    if not isinstance(result, Mapping):
        raise TypeError
    return cast(Mapping[str, object], result)


def _text(value: Mapping[str, object], key: str) -> str:
    result = value[key]
    if type(result) is not str or not result:
        raise ValueError
    return result


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _evidence(value: str) -> EvidenceDigest:
    return EvidenceDigest(value)
