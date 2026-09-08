from __future__ import annotations

import base64
import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import pytest
from kubernetes.client.exceptions import ApiException
from pytest_mock import MockerFixture

from markweave.broker import kubernetes_api as kubernetes_api_module
from markweave.broker.kubernetes_api import (
    KubernetesApiConfig,
    KubernetesApiControlPlane,
)
from markweave.broker.kubernetes_runtime import KubernetesRuntimeError
from markweave.broker.models import EvidenceDigest, RuntimeChannelLimits
from markweave.reversions.attempt_channel import (
    encode_channel_state,
    encode_response_metadata,
)
from markweave.reversions.errors import ReverseErrorCategory
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptRequest,
    ReverseAttemptSuccess,
    ReverseContentLimits,
    ReverseOutputMode,
)

ATTEMPT = UUID("11111111-1111-4111-8111-111111111111")
UNIT = UUID("22222222-2222-4222-8222-222222222222")
PRINCIPAL = UUID("33333333-3333-4333-8333-333333333333")
POD_UID = UUID("44444444-4444-4444-8444-444444444444")
SPECIFICATION = EvidenceDigest(f"sha256:{'5' * 64}")
CONFIG = KubernetesApiConfig(
    "markweave-reverse", 5.0, 5.0, 0.01, RuntimeChannelLimits(1_000_000, 2_000_000)
)


def _manifest() -> dict[str, object]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"markweave-reverse-{UNIT.hex}",
            "namespace": CONFIG.namespace,
            "uid": str(POD_UID),
            "labels": {
                "reverse.markweave.dev/managed": "1",
                "reverse.markweave.dev/unit-id": str(UNIT),
                "reverse.markweave.dev/attempt-id": str(ATTEMPT),
                "reverse.markweave.dev/principal-id": str(PRINCIPAL),
                "reverse.markweave.dev/policy-revision": "policy-v1",
            },
            "annotations": {
                "reverse.markweave.dev/policy-specification": SPECIFICATION.value
            },
        },
        "spec": {"nodeName": "reverse-node-1"},
    }


def _request(source: bytes = b"private source") -> ReverseAttemptRequest:
    return ReverseAttemptRequest(
        ATTEMPT,
        ".docx",
        ReverseContentLimits(
            1_000_000,
            2_000_000,
            100_000,
            1_000,
            1_000,
            1_000_000,
            1_000,
            32,
            16,
            500_000,
            1_000_000,
            1_000_000,
            2_000_000,
        ),
        source,
    )


def _pod_name(value: dict[str, object]) -> str:
    metadata = cast(dict[str, object], value["metadata"])
    return cast(str, metadata["name"])


class _Serializer:
    @staticmethod
    def sanitize_for_serialization(value: object) -> object:
        return deepcopy(value)


class _Api:
    def __init__(self) -> None:
        self.api_client = _Serializer()
        self.pod = _manifest()
        self.created: object | None = None
        self.deleted: Any | None = None
        self.present = True

    def create_namespaced_pod(
        self, namespace: str, body: object, **kwargs: object
    ) -> object:
        assert namespace == CONFIG.namespace
        self.created = deepcopy(body)
        return deepcopy(self.pod)

    def read_namespaced_pod(
        self, name: str, namespace: str, **kwargs: object
    ) -> object:
        del kwargs
        assert name == _pod_name(self.pod)
        assert namespace == CONFIG.namespace
        if not self.present:
            raise ApiException(status=404)
        return deepcopy(self.pod)

    def list_namespaced_pod(self, namespace: str, **kwargs: object) -> object:
        assert namespace == CONFIG.namespace
        assert kwargs["label_selector"] == "reverse.markweave.dev/managed=1"
        return SimpleNamespace(items=[deepcopy(self.pod)] if self.present else [])

    def delete_namespaced_pod(
        self, name: str, namespace: str, **kwargs: object
    ) -> object:
        assert name == _pod_name(self.pod)
        assert namespace == CONFIG.namespace
        self.deleted = kwargs["body"]
        self.present = False
        return object()

    def connect_get_namespaced_pod_exec(
        self, *args: object, **kwargs: object
    ) -> object:
        del args, kwargs
        raise AssertionError("stream factory must own exec")


class _ExecSession:
    def __init__(self, command: list[str], workspace: dict[str, bytes]) -> None:
        self.command = command
        self.workspace = workspace
        self.input = ""
        self.output = ""
        self.open = True

    def write_stdin(self, data: str) -> None:
        self.input += data

    def close_stdin(self) -> None:
        if "SIGKILL" in self.command[2]:
            return
        if self.command[2].startswith("import base64,json,os,sys"):
            payload = json.loads(self.input)
            self.workspace.update(
                {
                    "request.json": base64.b64decode(payload["request"]),
                    "source.bin": base64.b64decode(payload["source"]),
                    "response.state": base64.b64decode(payload["state"]),
                    "request.commit": base64.b64decode(payload["commit"]),
                }
            )
        else:
            leaf = self.command[4]
            self.output = base64.b64encode(self.workspace[leaf]).decode("ascii")

    def is_open(self) -> bool:
        return self.open

    def update(self, timeout: float = 0) -> None:
        del timeout
        self.open = False

    def peek_stdout(self) -> bool:
        return bool(self.output)

    def read_stdout(self, timeout: float | None = None) -> str:
        del timeout
        result, self.output = self.output, ""
        return result

    def peek_stderr(self) -> bool:
        return False

    def read_stderr(self, timeout: float | None = None) -> str:
        del timeout
        return ""

    @property
    def returncode(self) -> int:
        return 0

    def close(self, **kwargs: object) -> None:
        del kwargs
        self.open = False


class _ExecFactory:
    def __init__(self) -> None:
        self.workspace: dict[str, bytes] = {}
        self.commands: list[list[str]] = []

    def __call__(
        self, method: object, name: str, namespace: str, **kwargs: object
    ) -> _ExecSession:
        del method
        assert name == _pod_name(_manifest())
        assert namespace == CONFIG.namespace
        assert kwargs["container"] == "attempt"
        command = kwargs["command"]
        assert isinstance(command, list)
        self.commands.append(command)
        return _ExecSession(command, self.workspace)


@pytest.mark.unit
def test_control_plane_uses_exact_namespace_identity_and_fixed_exec_contract() -> None:
    api = _Api()
    executions = _ExecFactory()
    control = KubernetesApiControlPlane(api, CONFIG, exec_factory=executions)

    pod = control.create(_manifest())
    request = _request()
    control.stage_request(pod, request)
    assert executions.workspace["source.bin"] == request.source
    assert executions.workspace["request.commit"] == b"committed\n"
    assert control.try_collect_response(pod, ATTEMPT) is None

    response = ReverseAttemptSuccess(ATTEMPT, ReverseOutputMode.MARKDOWN, b"# result\n")
    executions.workspace.update(
        {
            "response.state": encode_channel_state(ATTEMPT, "complete"),
            "response.json": encode_response_metadata(response),
            "result.bin": response.result,
        }
    )
    assert control.try_collect_response(pod, ATTEMPT) == response

    control.terminate(pod)
    control.delete(pod)
    assert api.deleted is not None
    assert api.deleted.preconditions.uid == str(POD_UID)
    assert control.absent(pod) is True
    control.delete(pod)
    assert any("SIGKILL" in command[2] for command in executions.commands)


@pytest.mark.unit
def test_discovery_is_bounded_and_rejects_identity_substitution() -> None:
    api = _Api()
    control = KubernetesApiControlPlane(api, CONFIG, exec_factory=_ExecFactory())
    expected = control.create(_manifest())
    assert control.discover(
        namespace=CONFIG.namespace,
        labels={"reverse.markweave.dev/managed": "1"},
        limit=1,
    ) == (expected,)

    metadata = api.pod["metadata"]
    assert isinstance(metadata, dict)
    metadata["uid"] = str(UUID(int=99))
    with pytest.raises(KubernetesRuntimeError, match="identity changed"):
        control.absent(expected)


@pytest.mark.unit
def test_control_plane_fails_closed_at_api_and_channel_bounds() -> None:
    api = _Api()
    executions = _ExecFactory()
    control = KubernetesApiControlPlane(api, CONFIG, exec_factory=executions)
    manifest = _manifest()
    metadata = manifest["metadata"]
    assert isinstance(metadata, dict)
    metadata["namespace"] = "other"
    with pytest.raises(KubernetesRuntimeError, match="create request"):
        control.create(manifest)

    pod = control.create(_manifest())
    with pytest.raises(ValueError):
        control.stage_request(
            pod, _request(b"x" * (CONFIG.channel_limits.max_input_bytes + 1))
        )

    class UnboundedApi(_Api):
        def list_namespaced_pod(self, namespace: str, **kwargs: object) -> object:
            del namespace, kwargs
            return SimpleNamespace(items=[self.pod, self.pod])

    control = KubernetesApiControlPlane(UnboundedApi(), CONFIG, exec_factory=executions)
    with pytest.raises(KubernetesRuntimeError, match="limit"):
        control.discover(
            namespace=CONFIG.namespace,
            labels={"reverse.markweave.dev/managed": "1"},
            limit=1,
        )

    class ContinuedApi(_Api):
        def list_namespaced_pod(self, namespace: str, **kwargs: object) -> object:
            del namespace, kwargs
            return SimpleNamespace(
                items=[self.pod],
                metadata=SimpleNamespace(_continue="opaque", remaining_item_count=1),
            )

    control = KubernetesApiControlPlane(ContinuedApi(), CONFIG, exec_factory=executions)
    with pytest.raises(KubernetesRuntimeError, match="limit"):
        control.discover(
            namespace=CONFIG.namespace,
            labels={"reverse.markweave.dev/managed": "1"},
            limit=1,
        )


@pytest.mark.unit
def test_adapter_errors_do_not_leak_api_details() -> None:
    class FailingApi(_Api):
        def create_namespaced_pod(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            raise RuntimeError("private-document-marker")

    control = KubernetesApiControlPlane(
        FailingApi(), CONFIG, exec_factory=_ExecFactory()
    )
    with pytest.raises(KubernetesRuntimeError, match="creation failed") as raised:
        control.create(_manifest())
    assert "private-document-marker" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.unit
def test_create_waits_for_bounded_node_assignment() -> None:
    class SchedulingApi(_Api):
        def __init__(self) -> None:
            super().__init__()
            specification = cast(dict[str, object], self.pod["spec"])
            specification.pop("nodeName")
            self.reads = 0

        def read_namespaced_pod(
            self, name: str, namespace: str, **kwargs: object
        ) -> object:
            self.reads += 1
            if self.reads == 1:
                cast(dict[str, object], self.pod["spec"])["nodeName"] = "reverse-node-1"
            return super().read_namespaced_pod(name, namespace, **kwargs)

    api = SchedulingApi()
    slept: list[float] = []
    control = KubernetesApiControlPlane(
        api,
        CONFIG,
        exec_factory=_ExecFactory(),
        monotonic=lambda: 0.0,
        sleep=slept.append,
    )
    assert control.create(_manifest()).node_name == "reverse-node-1"
    assert slept == [CONFIG.poll_interval_seconds]


@pytest.mark.unit
def test_create_timeout_and_malformed_api_objects_fail_closed() -> None:
    api = _Api()
    cast(dict[str, object], api.pod["spec"]).pop("nodeName")
    moments = iter((0.0, 6.0))
    control = KubernetesApiControlPlane(
        api,
        CONFIG,
        exec_factory=_ExecFactory(),
        monotonic=lambda: next(moments),
    )
    with pytest.raises(KubernetesRuntimeError, match="scheduling timed out"):
        control.create(_manifest())

    api = _Api()
    api.api_client = cast(Any, SimpleNamespace(sanitize_for_serialization=lambda _: []))
    control = KubernetesApiControlPlane(api, CONFIG, exec_factory=_ExecFactory())
    with pytest.raises(KubernetesRuntimeError, match="identity is invalid"):
        control.create(_manifest())


@pytest.mark.unit
def test_exec_timeout_and_output_bounds_fail_closed() -> None:
    class BadSession(_ExecSession):
        def __init__(
            self, command: list[str], workspace: dict[str, bytes], mode: str
        ) -> None:
            super().__init__(command, workspace)
            self.mode = mode

        def update(self, timeout: float = 0) -> None:
            del timeout
            if self.mode == "stdout":
                self.output = "x" * (CONFIG.channel_limits.max_output_bytes * 2)
                self.open = False
            elif self.mode == "stderr":
                self.output = ""
                self.open = False

        def peek_stderr(self) -> bool:
            return self.mode == "stderr"

        def read_stderr(self, timeout: float | None = None) -> str:
            del timeout
            self.mode = "done"
            return "x" * 5000

    api = _Api()
    pod = KubernetesApiControlPlane(api, CONFIG, exec_factory=_ExecFactory()).create(
        _manifest()
    )
    for mode, expected in (("stdout", "output exceeds"), ("stderr", "error exceeds")):
        session = BadSession(["python", "-c", "noop"], {}, mode)

        def factory(
            *args: object, chosen: BadSession = session, **kwargs: object
        ) -> BadSession:
            del args
            chosen.command = cast(list[str], kwargs["command"])
            return chosen

        control = KubernetesApiControlPlane(api, CONFIG, exec_factory=factory)
        with pytest.raises(KubernetesRuntimeError, match=expected):
            control.stage_request(pod, _request())

    session = BadSession(["python", "-c", "noop"], {}, "timeout")
    moments = iter((0.0, 6.0))

    def timeout_factory(*args: object, **kwargs: object) -> BadSession:
        del args
        session.command = cast(list[str], kwargs["command"])
        return session

    control = KubernetesApiControlPlane(
        api,
        CONFIG,
        exec_factory=timeout_factory,
        monotonic=lambda: next(moments),
    )
    with pytest.raises(KubernetesRuntimeError, match="timed out"):
        control.stage_request(pod, _request())


@pytest.mark.unit
def test_websocket_upgrade_receives_the_exact_connect_timeout(
    mocker: MockerFixture,
) -> None:
    observed: dict[str, object] = {}

    class StalledUpgrade:
        def connect(self, url: str, **kwargs: object) -> None:
            observed.update(url=url, **kwargs)
            raise TimeoutError("stalled websocket upgrade")

    mocker.patch.object(
        kubernetes_api_module.ws_client,
        "WebSocket",
        return_value=StalledUpgrade(),
    )
    configuration = SimpleNamespace(
        verify_ssl=False,
        ssl_ca_cert=None,
        assert_hostname=None,
        cert_file=None,
        key_file=None,
        tls_server_name=None,
        proxy=None,
        proxy_headers=None,
    )

    with pytest.raises(TimeoutError, match="stalled websocket upgrade"):
        kubernetes_api_module._bounded_create_websocket(
            configuration, "ws://node/exec", None, 0.25
        )

    assert observed["timeout"] == 0.25


@pytest.mark.unit
def test_exec_rechecks_uid_after_helper_channel_is_established() -> None:
    api = _Api()
    initial = KubernetesApiControlPlane(api, CONFIG, exec_factory=_ExecFactory())
    pod = initial.create(_manifest())
    session = _ExecSession(["python", "-c", "noop"], {})

    def substituting_factory(*args: object, **kwargs: object) -> _ExecSession:
        del args
        session.command = cast(list[str], kwargs["command"])
        cast(dict[str, object], api.pod["metadata"])["uid"] = str(UUID(int=99))
        return session

    control = KubernetesApiControlPlane(api, CONFIG, exec_factory=substituting_factory)
    with pytest.raises(KubernetesRuntimeError, match="identity changed"):
        control.stage_request(pod, _request())
    assert session.input == ""


@pytest.mark.unit
def test_adapter_validates_configuration_and_request_identities() -> None:
    with pytest.raises(ValueError, match="configuration"):
        KubernetesApiConfig(
            "", 1.0, 1.0, 1.0, RuntimeChannelLimits(1_000_000, 2_000_000)
        )
    with pytest.raises(ValueError, match="adapter"):
        KubernetesApiControlPlane(cast(Any, object()), CONFIG)

    api = _Api()
    control = KubernetesApiControlPlane(api, CONFIG, exec_factory=_ExecFactory())
    pod = control.create(_manifest())
    assert control.find(pod.name) == pod
    wrong = UUID(int=42)
    with pytest.raises(KubernetesRuntimeError, match="workspace request"):
        control.stage_request(pod, replace(_request(), attempt_id=wrong))
    with pytest.raises(KubernetesRuntimeError, match="workspace response"):
        control.try_collect_response(pod, wrong)
    with pytest.raises(KubernetesRuntimeError, match="discovery request"):
        control.discover(namespace="other", labels={}, limit=0)
    api.present = False
    assert control.find(pod.name) is None


@pytest.mark.unit
def test_api_operation_failures_are_normalized_without_causes() -> None:
    class FailingOperations(_Api):
        failure = ""

        def read_namespaced_pod(
            self, name: str, namespace: str, **kwargs: object
        ) -> object:
            if self.failure == "read":
                raise RuntimeError("private-document-marker")
            if self.failure == "absence":
                raise ApiException(status=500, reason="private-document-marker")
            return super().read_namespaced_pod(name, namespace, **kwargs)

        def delete_namespaced_pod(
            self, name: str, namespace: str, **kwargs: object
        ) -> object:
            del name, namespace, kwargs
            raise RuntimeError("private-document-marker")

        def list_namespaced_pod(self, namespace: str, **kwargs: object) -> object:
            del namespace, kwargs
            raise RuntimeError("private-document-marker")

    api = FailingOperations()
    control = KubernetesApiControlPlane(api, CONFIG, exec_factory=_ExecFactory())
    pod = control.create(_manifest())

    api.failure = "read"
    with pytest.raises(KubernetesRuntimeError, match="identity lookup") as read_error:
        control.stage_request(pod, _request())
    assert read_error.value.__cause__ is None

    api.failure = "absence"
    with pytest.raises(KubernetesRuntimeError, match="absence lookup") as absent_error:
        control.absent(pod)
    assert absent_error.value.__cause__ is None

    api.failure = ""
    with pytest.raises(KubernetesRuntimeError, match="deletion failed") as delete_error:
        control.delete(pod)
    assert delete_error.value.__cause__ is None

    with pytest.raises(
        KubernetesRuntimeError, match="discovery failed"
    ) as discover_error:
        control.discover(
            namespace=CONFIG.namespace,
            labels={"reverse.markweave.dev/managed": "1"},
            limit=1,
        )
    assert discover_error.value.__cause__ is None


@pytest.mark.unit
def test_delete_treats_not_found_reply_as_idempotent_success() -> None:
    class LostDeleteReplyApi(_Api):
        def delete_namespaced_pod(
            self, name: str, namespace: str, **kwargs: object
        ) -> object:
            del name, namespace, kwargs
            self.present = False
            raise ApiException(status=404)

    api = LostDeleteReplyApi()
    control = KubernetesApiControlPlane(api, CONFIG, exec_factory=_ExecFactory())
    pod = control.create(_manifest())
    control.delete(pod)
    control.delete(pod)


@pytest.mark.unit
def test_delete_polls_for_api_absence_within_its_deadline() -> None:
    class DelayedDeletionApi(_Api):
        deleting = False
        post_delete_reads = 0

        def delete_namespaced_pod(
            self, name: str, namespace: str, **kwargs: object
        ) -> object:
            del name, namespace, kwargs
            self.deleting = True
            return object()

        def read_namespaced_pod(
            self, name: str, namespace: str, **kwargs: object
        ) -> object:
            if self.deleting:
                self.post_delete_reads += 1
                if self.post_delete_reads > 1:
                    raise ApiException(status=404)
            return super().read_namespaced_pod(name, namespace, **kwargs)

    api = DelayedDeletionApi()
    slept: list[float] = []
    control = KubernetesApiControlPlane(
        api,
        CONFIG,
        exec_factory=_ExecFactory(),
        monotonic=lambda: 0.0,
        sleep=slept.append,
    )
    pod = control.create(_manifest())
    control.delete(pod)
    assert slept == [CONFIG.poll_interval_seconds]


@pytest.mark.unit
def test_workspace_collection_rejects_malformed_and_substituted_results() -> None:
    api = _Api()
    executions = _ExecFactory()
    control = KubernetesApiControlPlane(api, CONFIG, exec_factory=executions)
    pod = control.create(_manifest())
    assert control.absent(pod) is False

    executions.workspace["response.state"] = encode_channel_state(ATTEMPT, "complete")
    executions.workspace["response.json"] = b"{"
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        control.try_collect_response(pod, ATTEMPT)

    substituted = ReverseAttemptFailure(
        UUID(int=73), ReverseErrorCategory.PROTOCOL_ERROR
    )
    executions.workspace["response.json"] = encode_response_metadata(substituted)
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        control.try_collect_response(pod, ATTEMPT)


@pytest.mark.unit
def test_malformed_api_identity_and_exec_status_fail_closed() -> None:
    api = _Api()
    malformed = deepcopy(api.pod)
    cast(dict[str, object], malformed["metadata"])["namespace"] = "other"
    api.pod = malformed
    control = KubernetesApiControlPlane(api, CONFIG, exec_factory=_ExecFactory())
    with pytest.raises(KubernetesRuntimeError, match="identity is invalid"):
        control.create(_manifest())

    class FailedSession(_ExecSession):
        @property
        def returncode(self) -> int:
            return 7

    api = _Api()
    pod = KubernetesApiControlPlane(api, CONFIG, exec_factory=_ExecFactory()).create(
        _manifest()
    )
    session = FailedSession(["python", "-c", "noop"], {})

    def factory(*args: object, **kwargs: object) -> FailedSession:
        del args
        session.command = cast(list[str], kwargs["command"])
        return session

    control = KubernetesApiControlPlane(api, CONFIG, exec_factory=factory)
    with pytest.raises(KubernetesRuntimeError, match="exec failed"):
        control.stage_request(pod, _request())
