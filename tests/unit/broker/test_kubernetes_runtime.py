from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest

from markweave.broker.kubernetes_attester import (
    CriCgroupInspector,
    NodeAttestationEngine,
    NodeFenceSnapshot,
    RemovalSnapshot,
    SandboxSnapshot,
)
from markweave.broker.kubernetes_runtime import (
    KubernetesIsolationRuntime,
    KubernetesPodIdentity,
    KubernetesRuntimeConfig,
    KubernetesRuntimeError,
    KubernetesRuntimeUnit,
    KubernetesSandboxIdentity,
    manifest_digest,
)
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    RuntimeChannelLimits,
    RuntimeIncarnation,
    RuntimeLimits,
    policy_specification_evidence,
)
from markweave.reversions.models import (
    ReverseAttemptRequest,
    ReverseAttemptResponse,
    ReverseContentLimits,
)
from tests.unit.broker.runtime_conformance import assert_lifecycle_conformance

ATTEMPT_ID = UUID("11111111-1111-4111-8111-111111111111")
UNIT_ID = UUID("22222222-2222-4222-8222-222222222222")
PRINCIPAL_ID = UUID("33333333-3333-4333-8333-333333333333")
POD_UID = UUID("44444444-4444-4444-8444-444444444444")
NODE_UID = UUID("55555555-5555-4555-8555-555555555555")
IMAGE_DIGEST = f"sha256:{'6' * 64}"
EVIDENCE = EvidenceDigest(f"sha256:{'7' * 64}")
SANDBOX_ID = "8" * 64
CGROUP = f"/kubepods.slice/pod{POD_UID}/sandbox.scope"
CONFIG = KubernetesRuntimeConfig(
    "markweave-reverse",
    "markweave-reverse-attempt",
    "markweave-reverse",
    "reverse",
    "fence-v1",
    1001,
    1001,
)


@pytest.fixture
def policy() -> BrokerPolicy:
    return BrokerPolicy(
        "t74-test",
        IMAGE_DIGEST,
        RuntimeLimits(100_000, 100_000, 268_435_456, 31, 16_777_216, 2_001),
        RuntimeChannelLimits(1_000_000, 2_000_000),
    )


@pytest.fixture
def unit(policy: BrokerPolicy) -> ManagedUnit:
    return ManagedUnit(
        ATTEMPT_ID,
        UNIT_ID,
        AuthenticatedPrincipal(PRINCIPAL_ID),
        1,
        policy.revision,
        policy_specification_evidence(policy),
        ManagedUnitState.CREATE_INTENT,
        1,
    )


def _request() -> ReverseAttemptRequest:
    return ReverseAttemptRequest(
        ATTEMPT_ID,
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
        b"private input",
    )


class ControlPlaneDouble:
    def __init__(self, unit: ManagedUnit) -> None:
        self.unit = unit
        self.manifest: dict[str, object] | None = None
        self.present = False
        self.staged: ReverseAttemptRequest | None = None
        self.response: ReverseAttemptResponse | None = None
        self.terminated = False
        self.tamper_manifest = False
        self.pod_uid = POD_UID
        self.fail_operation: str | None = None

    def identity(self) -> KubernetesPodIdentity:
        return KubernetesPodIdentity(
            "markweave-reverse",
            f"markweave-reverse-{self.unit.unit_id.hex}",
            self.pod_uid,
            "reverse-node-1",
            NODE_UID,
            self.unit.unit_id,
            self.unit.attempt_id,
            self.unit.principal.principal_id,
            self.unit.policy_revision,
            self.unit.policy_specification,
        )

    def create(self, manifest: Mapping[str, object]) -> KubernetesPodIdentity:
        if self.fail_operation == "create":
            raise RuntimeError("content-free injected failure")
        self.manifest = deepcopy(dict(manifest))
        self.present = True
        return self.identity()

    def stage_request(
        self, pod: KubernetesPodIdentity, request: ReverseAttemptRequest
    ) -> None:
        if self.fail_operation == "stage":
            raise RuntimeError("content-free injected failure")
        assert pod == self.identity()
        self.staged = request

    def try_collect_response(
        self, pod: KubernetesPodIdentity, expected_attempt_id: UUID
    ) -> ReverseAttemptResponse | None:
        if self.fail_operation == "collect":
            raise RuntimeError("content-free injected failure")
        assert pod == self.identity()
        assert expected_attempt_id == ATTEMPT_ID
        return self.response

    def terminate(self, pod: KubernetesPodIdentity) -> None:
        if self.fail_operation == "terminate":
            raise RuntimeError("content-free injected failure")
        assert pod == self.identity()
        self.terminated = True

    def delete(self, pod: KubernetesPodIdentity) -> None:
        if self.fail_operation == "delete":
            raise RuntimeError("content-free injected failure")
        assert pod == self.identity()
        self.present = False

    def absent(self, pod: KubernetesPodIdentity) -> bool:
        assert pod == self.identity()
        return not self.present

    def discover(
        self, *, namespace: str, labels: Mapping[str, str], limit: int
    ) -> tuple[KubernetesPodIdentity, ...]:
        if self.fail_operation == "discover":
            raise RuntimeError("content-free injected failure")
        if self.fail_operation == "discover":
            raise RuntimeError("content-free injected failure")
        assert namespace == "markweave-reverse"
        assert labels == {"reverse.markweave.dev/managed": "1"}
        assert limit > 0
        return (self.identity(),) if self.present else ()


class InspectorDouble:
    def __init__(self, control: ControlPlaneDouble, policy: BrokerPolicy) -> None:
        self.control = control
        self.policy = policy
        self.override_node: dict[str, Any] = {}
        self.override_sandbox: dict[str, Any] = {}
        self.override_removal: dict[str, Any] = {}
        self.fail_operation: str | None = None

    def node_fence(self, node_name: str) -> NodeFenceSnapshot:
        if self.fail_operation == "node":
            raise RuntimeError("content-free injected failure")
        values: dict[str, Any] = {
            "node_name": node_name,
            "node_uid": NODE_UID,
            "ready": True,
            "pool_name": "reverse",
            "fence_revision": "fence-v1",
            "dedicated_taint_value": "reverse",
            "cgroup_version": 2,
            "pod_pids_limit": self.policy.limits.pid_limit,
            "cpu_quota_period_micros": self.policy.limits.cpu_period_micros,
            "network_interfaces": ("lo",),
            "forwarding_enabled": False,
        }
        values.update(self.override_node)
        return NodeFenceSnapshot(**values)

    def sandbox(self, pod_uid: UUID) -> SandboxSnapshot:
        if self.fail_operation == "sandbox":
            raise RuntimeError("content-free injected failure")
        running = not self.control.terminated
        states = ("RUNNING",) if running else ("EXITED",)
        assert self.control.manifest is not None
        observed_manifest = deepcopy(self.control.manifest)
        if self.control.tamper_manifest:
            specification = observed_manifest["spec"]
            assert isinstance(specification, dict)
            specification["hostNetwork"] = True
        values: dict[str, Any] = {
            "pod_uid": pod_uid,
            "node_uid": NODE_UID,
            "sandbox_id": SANDBOX_ID,
            "cgroup_path": CGROUP,
            "observed_manifest": observed_manifest,
            "container_ids": ("9" * 64,),
            "container_states": states,
            "sandbox_ready": running,
            "cgroup_populated": running,
            "descendant_pids": (123,) if running else (),
        }
        values.update(self.override_sandbox)
        return SandboxSnapshot(**values)

    def removal(self, pod_uid: UUID) -> RemovalSnapshot:
        if self.fail_operation == "removal":
            raise RuntimeError("content-free injected failure")
        values: dict[str, Any] = {
            "pod_uid": pod_uid,
            "node_uid": NODE_UID,
            "sandbox_id": SANDBOX_ID,
            "cgroup_path": CGROUP,
            "cri_lookup_complete": True,
            "sandbox_present": False,
            "cgroup_lookup_complete": True,
            "cgroup_present": False,
        }
        values.update(self.override_removal)
        return RemovalSnapshot(**values)


def _runtime(
    unit: ManagedUnit,
    policy: BrokerPolicy,
    control: ControlPlaneDouble | None = None,
    inspector: InspectorDouble | None = None,
) -> tuple[KubernetesIsolationRuntime, ControlPlaneDouble, InspectorDouble]:
    control = control or ControlPlaneDouble(unit)
    inspector = inspector or InspectorDouble(control, policy)
    runtime = KubernetesIsolationRuntime(
        image_repository="registry.example/markweave-reverse-attempt",
        policy=policy,
        config=CONFIG,
        control_plane=control,
        node_attester=NodeAttestationEngine(inspector),
    )
    return runtime, control, inspector


@pytest.mark.unit
def test_kubernetes_manifest_enforces_every_fixed_boundary(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, _ = _runtime(unit, policy)

    result = runtime.create(unit, policy)

    assert result.unit_id == UNIT_ID
    manifest = control.manifest
    assert manifest is not None
    specification = manifest["spec"]
    assert isinstance(specification, dict)
    assert specification["activeDeadlineSeconds"] == 3
    assert specification["automountServiceAccountToken"] is False
    assert specification["enableServiceLinks"] is False
    assert specification["hostIPC"] is False
    assert specification["hostNetwork"] is False
    assert specification["hostPID"] is False
    assert specification["restartPolicy"] == "Never"
    assert specification["runtimeClassName"] == "markweave-reverse"
    assert specification["terminationGracePeriodSeconds"] == 0
    assert specification["nodeSelector"] == {
        "reverse.markweave.dev/isolation-pool": "reverse",
        "reverse.markweave.dev/node-fence": "fence-v1",
    }
    assert specification["volumes"] == [
        {
            "name": "work",
            "emptyDir": {"medium": "Memory", "sizeLimit": "16777216"},
        }
    ]
    container = specification["containers"][0]
    assert container["image"] == (
        "registry.example/markweave-reverse-attempt@" + IMAGE_DIGEST
    )
    assert container["command"] == [
        "python",
        "-m",
        "markweave.reversions.attempt_main",
    ]
    assert container["args"] == []
    assert container["resources"]["limits"] == {
        "cpu": "1000m",
        "memory": "268435456",
        "ephemeral-storage": "16777216",
    }
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "privileged": False,
        "readOnlyRootFilesystem": True,
        "runAsGroup": 1001,
        "runAsNonRoot": True,
        "runAsUser": 1001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    assert "secret" not in json_repr(manifest).lower()


@pytest.mark.unit
def test_kubernetes_backend_satisfies_shared_lifecycle(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, _, _ = _runtime(unit, policy)
    assert_lifecycle_conformance(runtime, unit, policy)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"network_interfaces": ("lo", "eth0")}, "node fence"),
        ({"pod_pids_limit": 32}, "node fence"),
        ({"ready": False}, "node fence"),
        ({"fence_revision": "other"}, "node fence"),
    ],
)
def test_node_fence_fails_closed(
    unit: ManagedUnit,
    policy: BrokerPolicy,
    override: dict[str, object],
    message: str,
) -> None:
    runtime, _, inspector = _runtime(unit, policy)
    inspector.override_node = override

    with pytest.raises(KubernetesRuntimeError, match=message):
        runtime.create(unit, policy)


@pytest.mark.unit
def test_manifest_substitution_and_inexact_cpu_fail_closed(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, inspector = _runtime(unit, policy)
    control.tamper_manifest = True
    with pytest.raises(KubernetesRuntimeError, match="sandbox attestation"):
        runtime.create(unit, policy)
    assert control.present is True

    inexact = replace(
        policy,
        limits=replace(policy.limits, cpu_quota_micros=100_001),
    )
    with pytest.raises(KubernetesRuntimeError, match="not exactly representable"):
        KubernetesIsolationRuntime(
            image_repository="registry.example/reverse",
            policy=inexact,
            config=CONFIG,
            control_plane=control,
            node_attester=NodeAttestationEngine(inspector),
        ).create(
            replace(unit, policy_specification=policy_specification_evidence(inexact)),
            inexact,
        )


@pytest.mark.unit
def test_removal_needs_empty_cri_cgroup_and_api_evidence(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, inspector = _runtime(unit, policy)
    runtime_unit = runtime.create(unit, policy)
    control.terminated = True
    empty = runtime.confirm_empty(runtime_unit)

    runtime.remove(runtime_unit)
    inspector.override_removal = {"cgroup_present": True}
    with pytest.raises(KubernetesRuntimeError, match="removal is unconfirmed"):
        runtime.confirm_removed(runtime_unit, empty)
    inspector.override_removal = {}
    control.present = True
    with pytest.raises(KubernetesRuntimeError, match="removal is unconfirmed"):
        runtime.confirm_removed(runtime_unit, empty)


@pytest.mark.unit
def test_restart_discovery_rebinds_cri_identity(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    first, control, inspector = _runtime(unit, policy)
    expected = first.create(unit, policy)
    restarted = KubernetesIsolationRuntime(
        image_repository="registry.example/markweave-reverse-attempt",
        policy=policy,
        config=CONFIG,
        control_plane=control,
        node_attester=NodeAttestationEngine(inspector),
    )

    assert restarted.discover(limit=1) == (expected,)


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory",
    [
        lambda: KubernetesRuntimeConfig(
            "Bad", "attempt", "runtime", "pool", "fence", 1001, 1001
        ),
        lambda: KubernetesRuntimeConfig(
            "valid", "attempt", "runtime", "pool", "fence", 0, 1001
        ),
        lambda: KubernetesPodIdentity(
            "Bad",
            "pod",
            POD_UID,
            "node",
            NODE_UID,
            UNIT_ID,
            ATTEMPT_ID,
            PRINCIPAL_ID,
            "policy",
            EVIDENCE,
        ),
        lambda: KubernetesSandboxIdentity("bad", CGROUP, EVIDENCE),
        lambda: KubernetesRuntimeUnit(
            UNIT_ID,
            RuntimeIncarnation(UNIT_ID, EVIDENCE),
            KubernetesPodIdentity(
                "valid",
                "pod",
                POD_UID,
                "node",
                NODE_UID,
                UNIT_ID,
                ATTEMPT_ID,
                PRINCIPAL_ID,
                "policy",
                EVIDENCE,
            ),
            KubernetesSandboxIdentity(SANDBOX_ID, CGROUP, EVIDENCE),
        ),
    ],
)
def test_kubernetes_identity_models_reject_invalid_values(
    factory: Callable[[], object],
) -> None:
    with pytest.raises(ValueError):
        factory()


@pytest.mark.unit
def test_workspace_delegation_and_identity_checks(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, _ = _runtime(unit, policy)
    runtime_unit = runtime.create(unit, policy)
    request = _request()

    runtime.stage_request(runtime_unit, request)
    assert control.staged == request
    assert runtime.try_collect_response(runtime_unit, ATTEMPT_ID) is None

    unknown = replace(runtime_unit, unit_id=UUID(int=99))
    with pytest.raises(KubernetesRuntimeError, match="unknown"):
        runtime.hard_terminate(unknown)
    with pytest.raises(KubernetesRuntimeError, match="request"):
        runtime.stage_request(runtime_unit, cast(ReverseAttemptRequest, object()))
    with pytest.raises(KubernetesRuntimeError, match="response"):
        runtime.try_collect_response(runtime_unit, cast(UUID, "not-a-uuid"))


@pytest.mark.unit
def test_exit_and_empty_require_positive_cri_and_cgroup_facts(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, inspector = _runtime(unit, policy)
    runtime_unit = runtime.create(unit, policy)

    with pytest.raises(KubernetesRuntimeError, match="exit is unconfirmed"):
        runtime.confirm_exit(runtime_unit)
    control.terminated = True
    inspector.override_sandbox = {"descendant_pids": (999,)}
    with pytest.raises(KubernetesRuntimeError, match="not empty"):
        runtime.confirm_empty(runtime_unit)
    inspector.override_sandbox = {"sandbox_id": "a" * 64}
    with pytest.raises(KubernetesRuntimeError, match="exit is unconfirmed"):
        runtime.confirm_exit(runtime_unit)


@pytest.mark.unit
def test_discovery_rejects_unbounded_or_substituted_results(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, _ = _runtime(unit, policy)
    runtime.create(unit, policy)
    with pytest.raises(KubernetesRuntimeError, match="limit"):
        runtime.discover(limit=0)

    control.pod_uid = UUID(int=101)
    with pytest.raises(KubernetesRuntimeError, match="identity"):
        runtime.discover(limit=1)


@pytest.mark.unit
def test_boundary_failures_are_content_free(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, inspector = _runtime(unit, policy)
    control.fail_operation = "create"
    with pytest.raises(KubernetesRuntimeError, match="create failed"):
        runtime.create(unit, policy)
    control.fail_operation = None
    runtime_unit = runtime.create(unit, policy)

    for operation, expected in (
        ("stage", "staging failed"),
        ("collect", "collection failed"),
        ("terminate", "termination failed"),
        ("delete", "removal failed"),
        ("discover", "discovery failed"),
    ):
        control.fail_operation = operation
        with pytest.raises(KubernetesRuntimeError, match=expected):
            if operation == "stage":
                runtime.stage_request(runtime_unit, _request())
            elif operation == "collect":
                runtime.try_collect_response(runtime_unit, ATTEMPT_ID)
            elif operation == "terminate":
                runtime.hard_terminate(runtime_unit)
            elif operation == "delete":
                runtime.remove(runtime_unit)
            else:
                runtime.discover(limit=1)
    control.fail_operation = None
    control.terminated = True
    empty = runtime.confirm_empty(runtime_unit)
    inspector.fail_operation = "removal"
    with pytest.raises(KubernetesRuntimeError, match="removal is unconfirmed"):
        runtime.confirm_removed(runtime_unit, empty)


@pytest.mark.unit
def test_configuration_and_manifest_validation_fail_closed(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    _, control, inspector = _runtime(unit, policy)
    with pytest.raises(ValueError, match="runtime configuration"):
        KubernetesIsolationRuntime(
            image_repository="repository@mutable",
            policy=policy,
            config=CONFIG,
            control_plane=control,
            node_attester=NodeAttestationEngine(inspector),
        )
    with pytest.raises(ValueError, match="node inspector"):
        NodeAttestationEngine(cast(CriCgroupInspector, object()))
    with pytest.raises(KubernetesRuntimeError, match="manifest is invalid"):
        manifest_digest({"invalid": object()})
    runtime, _, _ = _runtime(unit, policy)
    with pytest.raises(KubernetesRuntimeError, match="create contract"):
        runtime.create(replace(unit, state=ManagedUnitState.RESERVED), policy)


def json_repr(value: object) -> str:
    return json.dumps(value, sort_keys=True)
