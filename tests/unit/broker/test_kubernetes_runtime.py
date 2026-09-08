from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
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
    KubernetesAttestationContract,
    KubernetesAttestationNotReady,
    KubernetesIsolationRuntime,
    KubernetesPodIdentity,
    KubernetesRuntimeConfig,
    KubernetesRuntimeError,
    KubernetesRuntimeUnit,
    KubernetesSandboxIdentity,
    manifest_digest,
    pod_contract_digest,
    pod_contract_projection,
    project_observed_pod,
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
from markweave.broker.ports import RuntimeUnit
from markweave.reversions.errors import ReverseErrorCategory
from markweave.reversions.models import (
    ReverseAttemptFailure,
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
SENSITIVE_MARKER = "private-document-marker"
CONFIG = KubernetesRuntimeConfig(
    "markweave-reverse",
    "markweave-reverse-attempt",
    "markweave-reverse",
    "reverse",
    "fence-v1",
    1001,
    1001,
    67_108_864,
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
            self.unit.unit_id,
            self.unit.attempt_id,
            self.unit.principal.principal_id,
            self.unit.policy_revision,
            self.unit.policy_specification,
        )

    def create(self, manifest: Mapping[str, object]) -> KubernetesPodIdentity:
        if self.fail_operation == "create":
            raise RuntimeError(f"injected failure: {SENSITIVE_MARKER}")
        self.manifest = deepcopy(dict(manifest))
        self.present = True
        return self.identity()

    def find(self, name: str) -> KubernetesPodIdentity | None:
        if name != f"markweave-reverse-{self.unit.unit_id.hex}":
            raise KubernetesRuntimeError("Kubernetes Pod lookup is invalid")
        return self.identity() if self.present else None

    def stage_request(
        self, pod: KubernetesPodIdentity, request: ReverseAttemptRequest
    ) -> None:
        if self.fail_operation == "stage":
            raise RuntimeError(f"injected failure: {SENSITIVE_MARKER}")
        assert pod == self.identity()
        self.staged = request

    def try_collect_response(
        self, pod: KubernetesPodIdentity, expected_attempt_id: UUID
    ) -> ReverseAttemptResponse | None:
        if self.fail_operation == "collect":
            raise RuntimeError(f"injected failure: {SENSITIVE_MARKER}")
        assert pod == self.identity()
        assert expected_attempt_id == ATTEMPT_ID
        return self.response

    def terminate(self, pod: KubernetesPodIdentity) -> None:
        if self.fail_operation == "terminate":
            raise RuntimeError(f"injected failure: {SENSITIVE_MARKER}")
        assert pod == self.identity()
        self.terminated = True

    def delete(self, pod: KubernetesPodIdentity) -> None:
        if self.fail_operation == "delete":
            raise RuntimeError(f"injected failure: {SENSITIVE_MARKER}")
        assert pod == self.identity()
        self.present = False

    def absent(self, pod: KubernetesPodIdentity) -> bool:
        assert pod == self.identity()
        return not self.present

    def discover(
        self, *, namespace: str, labels: Mapping[str, str], limit: int
    ) -> tuple[KubernetesPodIdentity, ...]:
        if self.fail_operation == "discover":
            raise RuntimeError(f"injected failure: {SENSITIVE_MARKER}")
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
            raise RuntimeError(f"injected failure: {SENSITIVE_MARKER}")
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
        }
        values.update(self.override_node)
        return NodeFenceSnapshot(**values)

    def sandbox(self, pod_uid: UUID) -> SandboxSnapshot:
        if self.fail_operation == "not_ready":
            raise KubernetesAttestationNotReady("Kubernetes sandbox is not ready")
        if self.fail_operation == "sandbox":
            raise RuntimeError(f"injected failure: {SENSITIVE_MARKER}")
        running = not self.control.terminated
        states = ("RUNNING",) if running else ("EXITED",)
        assert self.control.manifest is not None
        observed_pod = deepcopy(self.control.manifest)
        metadata = observed_pod["metadata"]
        assert isinstance(metadata, dict)
        metadata["uid"] = str(pod_uid)
        specification = observed_pod["spec"]
        assert isinstance(specification, dict)
        specification["nodeName"] = "reverse-node-1"
        if self.control.tamper_manifest:
            specification["hostNetwork"] = True
        values: dict[str, Any] = {
            "pod_uid": pod_uid,
            "node_uid": NODE_UID,
            "sandbox_id": SANDBOX_ID,
            "cgroup_path": CGROUP,
            "observed_pod": observed_pod,
            "container_ids": ("9" * 64,),
            "container_states": states,
            "sandbox_ready": running,
            "network_interfaces": ("lo",),
            "forwarding_enabled": False,
            "cpu_quota_micros": self.policy.limits.cpu_quota_micros,
            "cpu_period_micros": self.policy.limits.cpu_period_micros,
            "memory_max_bytes": self.policy.limits.memory_bytes,
            "pids_max": self.policy.limits.pid_limit,
            "workspace_filesystem": "tmpfs",
            "workspace_mount_path": "/work",
            "workspace_size_bytes": self.policy.limits.workspace_bytes,
            "workspace_mount_flags": ("nodev", "noexec", "nosuid", "rw"),
            "cgroup_populated": running,
            "descendant_pids": (123,) if running else (),
        }
        values.update(self.override_sandbox)
        return SandboxSnapshot(**values)

    def removal(self, pod_uid: UUID) -> RemovalSnapshot:
        if self.fail_operation == "removal":
            raise RuntimeError(f"injected failure: {SENSITIVE_MARKER}")
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


class UnexpectedAttester:
    def bind(
        self, pod: KubernetesPodIdentity, contract: KubernetesAttestationContract
    ) -> KubernetesSandboxIdentity:
        del pod, contract
        raise RuntimeError(f"unexpected attester failure: {SENSITIVE_MARKER}")

    def confirm_exit(self, unit: KubernetesRuntimeUnit) -> EvidenceDigest:
        del unit
        return EVIDENCE

    def confirm_empty(
        self, unit: KubernetesRuntimeUnit, exit_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        del unit, exit_evidence
        return EVIDENCE

    def confirm_removed(
        self, unit: KubernetesRuntimeUnit, empty_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        del unit, empty_evidence
        return EVIDENCE

    def recover(
        self,
        unit: KubernetesRuntimeUnit,
        contract: KubernetesAttestationContract,
        lifecycle_state: ManagedUnitState,
    ) -> KubernetesSandboxIdentity:
        del unit, contract, lifecycle_state
        raise RuntimeError(f"unexpected attester failure: {SENSITIVE_MARKER}")

    def recover_create_intent(
        self,
        pod: KubernetesPodIdentity,
        proposed_contract: KubernetesAttestationContract,
    ) -> tuple[KubernetesSandboxIdentity, KubernetesAttestationContract]:
        del pod, proposed_contract
        raise RuntimeError(f"unexpected attester failure: {SENSITIVE_MARKER}")

    def adopt_create_intent(
        self,
        pod: KubernetesPodIdentity,
        contract: KubernetesAttestationContract,
    ) -> KubernetesSandboxIdentity:
        del pod, contract
        raise RuntimeError(f"unexpected attester failure: {SENSITIVE_MARKER}")

    def acknowledge(
        self, unit: KubernetesRuntimeUnit, removal_evidence: EvidenceDigest
    ) -> None:
        del unit, removal_evidence
        raise RuntimeError(f"unexpected attester failure: {SENSITIVE_MARKER}")


class RecoveredIntentAttester:
    def __init__(
        self,
        engine: NodeAttestationEngine,
        pod: KubernetesPodIdentity,
        sandbox: KubernetesSandboxIdentity,
        contract: KubernetesAttestationContract,
    ) -> None:
        self.engine = engine
        self.pod = pod
        self.sandbox = sandbox
        self.contract = contract

    def bind(
        self, pod: KubernetesPodIdentity, contract: KubernetesAttestationContract
    ) -> KubernetesSandboxIdentity:
        return self.engine.bind(pod, contract)

    def recover_create_intent(
        self,
        pod: KubernetesPodIdentity,
        proposed_contract: KubernetesAttestationContract,
    ) -> tuple[KubernetesSandboxIdentity, KubernetesAttestationContract]:
        del proposed_contract
        if pod != self.pod:
            raise KubernetesRuntimeError("Kubernetes recovery identity changed")
        return self.sandbox, self.contract

    def adopt_create_intent(
        self,
        pod: KubernetesPodIdentity,
        contract: KubernetesAttestationContract,
    ) -> KubernetesSandboxIdentity:
        del contract
        if pod != self.pod:
            raise KubernetesRuntimeError("Kubernetes recovery identity changed")
        return self.sandbox

    def recover(
        self,
        unit: KubernetesRuntimeUnit,
        contract: KubernetesAttestationContract,
        lifecycle_state: ManagedUnitState,
    ) -> KubernetesSandboxIdentity:
        return self.engine.recover(unit, contract, lifecycle_state)

    def confirm_exit(self, unit: KubernetesRuntimeUnit) -> EvidenceDigest:
        return self.engine.confirm_exit(unit)

    def confirm_empty(
        self, unit: KubernetesRuntimeUnit, exit_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        return self.engine.confirm_empty(unit, exit_evidence)

    def confirm_removed(
        self, unit: KubernetesRuntimeUnit, empty_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        return self.engine.confirm_removed(unit, empty_evidence)

    def acknowledge(
        self, unit: KubernetesRuntimeUnit, removal_evidence: EvidenceDigest
    ) -> None:
        self.engine.acknowledge(unit, removal_evidence)


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
    metadata = manifest["metadata"]
    assert isinstance(metadata, dict)
    labels = metadata["labels"]
    assert isinstance(labels, dict)
    assert "reverse.markweave.dev/policy-specification" not in labels
    assert metadata["annotations"] == {
        "reverse.markweave.dev/policy-specification": unit.policy_specification.value
    }
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
    assert specification["securityContext"] == {
        "fsGroup": 1001,
        "fsGroupChangePolicy": "OnRootMismatch",
        "runAsGroup": 1001,
        "runAsNonRoot": True,
        "runAsUser": 1001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
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
def test_observed_pod_projection_accepts_api_defaults_and_quantity_forms(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, _ = _runtime(unit, policy)
    runtime.create(unit, policy)
    assert control.manifest is not None
    expected = pod_contract_projection(control.manifest)
    observed = deepcopy(control.manifest)
    metadata = observed["metadata"]
    assert isinstance(metadata, dict)
    metadata["creationTimestamp"] = "2026-09-08T00:00:00Z"
    metadata["deletionTimestamp"] = "2026-09-08T00:01:00Z"
    metadata["deletionGracePeriodSeconds"] = 0
    metadata["generation"] = 1
    metadata["managedFields"] = []
    metadata["resourceVersion"] = "123"
    metadata["uid"] = str(POD_UID)
    specification = observed["spec"]
    assert isinstance(specification, dict)
    specification["nodeName"] = "reverse-node-1"
    specification["preemptionPolicy"] = "PreemptLowerPriority"
    specification["priority"] = 0
    specification["schedulerName"] = "default-scheduler"
    specification["serviceAccount"] = "markweave-reverse-attempt"
    tolerations = specification["tolerations"]
    assert isinstance(tolerations, list)
    tolerations.extend(
        [
            {
                "effect": "NoExecute",
                "key": "node.kubernetes.io/not-ready",
                "operator": "Exists",
                "tolerationSeconds": 300,
            },
            {
                "effect": "NoExecute",
                "key": "node.kubernetes.io/unreachable",
                "operator": "Exists",
                "tolerationSeconds": 300,
            },
        ]
    )
    container = specification["containers"][0]
    assert isinstance(container, dict)
    container["terminationMessagePath"] = "/dev/termination-log"
    container["terminationMessagePolicy"] = "File"
    resources = container["resources"]
    assert isinstance(resources, dict)
    for section_name in ("limits", "requests"):
        section = resources[section_name]
        assert isinstance(section, dict)
        section["cpu"] = "1"
        section["memory"] = "256Mi"
        section["ephemeral-storage"] = "16Mi"
    volumes = specification["volumes"]
    assert isinstance(volumes, list)
    volume = volumes[0]
    assert isinstance(volume, dict)
    empty_dir = volume["emptyDir"]
    assert isinstance(empty_dir, dict)
    empty_dir["sizeLimit"] = "16Mi"
    observed["status"] = {"phase": "Pending"}

    assert project_observed_pod(observed, expected) == expected


@pytest.mark.unit
def test_observed_pod_rejects_orphaned_deletion_grace_period(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, _ = _runtime(unit, policy)
    runtime.create(unit, policy)
    assert control.manifest is not None
    expected = pod_contract_projection(control.manifest)
    observed = deepcopy(control.manifest)
    metadata = observed["metadata"]
    assert isinstance(metadata, dict)
    metadata["deletionGracePeriodSeconds"] = 0

    with pytest.raises(KubernetesRuntimeError, match="observed Pod"):
        project_observed_pod(observed, expected)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cpu_quota_micros", 99_999),
        ("cpu_period_micros", 99_999),
        ("memory_max_bytes", 1),
        ("pids_max", 32),
        ("workspace_filesystem", "overlay"),
        ("workspace_mount_path", "/unexpected-work"),
        ("workspace_size_bytes", 1),
        ("workspace_mount_flags", ("rw",)),
        ("network_interfaces", ("eth0", "lo")),
        ("forwarding_enabled", True),
    ],
)
def test_sandbox_kernel_enforcement_must_match_exact_policy(
    unit: ManagedUnit, policy: BrokerPolicy, field: str, value: object
) -> None:
    runtime, _, inspector = _runtime(unit, policy)
    inspector.override_sandbox = {field: value}

    with pytest.raises(KubernetesRuntimeError, match="sandbox attestation"):
        runtime.create(unit, policy)


@pytest.mark.unit
@pytest.mark.parametrize("field", ["initContainers", "ephemeralContainers"])
def test_observed_pod_rejects_injected_workload_containers(
    unit: ManagedUnit, policy: BrokerPolicy, field: str
) -> None:
    runtime, control, _ = _runtime(unit, policy)
    runtime.create(unit, policy)
    assert control.manifest is not None
    observed = deepcopy(control.manifest)
    specification = observed["spec"]
    assert isinstance(specification, dict)
    specification[field] = [{"name": "injected", "image": "attacker/image"}]

    with pytest.raises(KubernetesRuntimeError, match="observed Pod"):
        project_observed_pod(observed, pod_contract_projection(control.manifest))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("spec",), []),
        (("spec", "tolerations"), ()),
        (("spec", "containers"), ()),
        (("spec", "containers", 0, "command"), ()),
        (("spec", "containers", 0, "resources", "limits", "cpu"), 1),
        (("spec", "containers", 0, "resources", "limits", "cpu"), "0m"),
        (("spec", "containers", 0, "resources", "limits", "memory"), 1),
        (("spec", "containers", 0, "resources", "limits", "memory"), "0Mi"),
        (
            ("spec", "containers", 0, "securityContext", "procMount"),
            "Unmasked",
        ),
        (
            ("spec", "containers", 0, "volumeMounts", 0, "mountPropagation"),
            "Bidirectional",
        ),
    ],
)
def test_observed_pod_rejects_malformed_security_contract_values(
    unit: ManagedUnit,
    policy: BrokerPolicy,
    path: tuple[str | int, ...],
    value: object,
) -> None:
    runtime, control, _ = _runtime(unit, policy)
    runtime.create(unit, policy)
    assert control.manifest is not None
    expected = pod_contract_projection(control.manifest)
    observed = deepcopy(control.manifest)
    target: object = observed
    for segment in path[:-1]:
        if isinstance(segment, int):
            assert isinstance(target, list)
            target = target[segment]
        else:
            assert isinstance(target, dict)
            target = target[segment]
    final = path[-1]
    if isinstance(final, int):
        assert isinstance(target, list)
        target[final] = value
    else:
        assert isinstance(target, dict)
        target[final] = value

    with pytest.raises(KubernetesRuntimeError, match="observed Pod"):
        project_observed_pod(observed, expected)


@pytest.mark.unit
def test_observed_pod_rejects_arbitrary_tolerations(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, _ = _runtime(unit, policy)
    runtime.create(unit, policy)
    assert control.manifest is not None
    expected = pod_contract_projection(control.manifest)
    observed = deepcopy(control.manifest)
    specification = observed["spec"]
    assert isinstance(specification, dict)
    tolerations = specification["tolerations"]
    assert isinstance(tolerations, list)
    tolerations.append(
        {
            "effect": "NoSchedule",
            "key": "tenant.example/bypass",
            "operator": "Exists",
        }
    )

    with pytest.raises(KubernetesRuntimeError, match="observed Pod"):
        project_observed_pod(observed, expected)


@pytest.mark.unit
@pytest.mark.parametrize("identity_field", ["pod_uid", "node_name"])
def test_attester_rejects_observed_api_identity_substitution(
    unit: ManagedUnit, policy: BrokerPolicy, identity_field: str
) -> None:
    runtime, control, inspector = _runtime(unit, policy)
    runtime.create(unit, policy)
    assert control.manifest is not None
    observed = deepcopy(control.manifest)
    metadata = observed["metadata"]
    specification = observed["spec"]
    assert isinstance(metadata, dict)
    assert isinstance(specification, dict)
    metadata["uid"] = str(POD_UID if identity_field != "pod_uid" else UUID(int=97))
    specification["nodeName"] = (
        "reverse-node-1" if identity_field != "node_name" else "substituted-node"
    )
    inspector.override_sandbox = {"observed_pod": observed}

    with pytest.raises(KubernetesRuntimeError, match="sandbox attestation"):
        runtime.discover(limit=1)


@pytest.mark.unit
def test_pod_contract_validation_rejects_invalid_top_level_shapes(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, _ = _runtime(unit, policy)
    runtime.create(unit, policy)
    assert control.manifest is not None
    expected = pod_contract_projection(control.manifest)
    assert pod_contract_digest(control.manifest) == manifest_digest(expected)

    with pytest.raises(KubernetesRuntimeError, match="Pod contract"):
        pod_contract_projection(cast(Mapping[str, object], []))
    with pytest.raises(KubernetesRuntimeError, match="Pod contract"):
        pod_contract_projection(cast(Mapping[str, object], {1: "invalid"}))
    with pytest.raises(KubernetesRuntimeError, match="observed Pod"):
        project_observed_pod(
            {"spec": {"tolerations": []}},
            {"spec": cast(object, [])},
        )
    for malformed in (None, []):
        with pytest.raises(KubernetesRuntimeError, match="observed Pod"):
            project_observed_pod(
                cast(Mapping[str, object], malformed),
                {"spec": {"tolerations": []}},
            )
    with pytest.raises(KubernetesRuntimeError, match="observed Pod"):
        project_observed_pod(
            cast(Mapping[str, object], {1: "invalid", "spec": {"tolerations": []}}),
            cast(Mapping[str, object], {1: "invalid", "spec": {"tolerations": []}}),
        )


@pytest.mark.unit
def test_attester_public_proof_operations_validate_inputs_and_inspector_failures(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, _, inspector = _runtime(unit, policy)
    inspector.fail_operation = "node"
    with pytest.raises(KubernetesRuntimeError, match="node attestation failed"):
        runtime.create(unit, policy)

    inspector.fail_operation = None
    runtime_unit = runtime.create(unit, policy)
    engine = NodeAttestationEngine(inspector)
    with pytest.raises(KubernetesRuntimeError, match="exit evidence"):
        engine.confirm_empty(runtime_unit, cast(EvidenceDigest, object()))
    with pytest.raises(KubernetesRuntimeError, match="empty evidence"):
        engine.confirm_removed(runtime_unit, cast(EvidenceDigest, object()))
    with pytest.raises(KubernetesRuntimeError, match="runtime unit"):
        engine.confirm_exit(cast(KubernetesRuntimeUnit, object()))

    inspector.fail_operation = "sandbox"
    with pytest.raises(KubernetesRuntimeError, match="exit is unconfirmed"):
        runtime.confirm_exit(runtime_unit)


@pytest.mark.unit
def test_attester_rejects_invalid_cri_sandbox_identity(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, _, inspector = _runtime(unit, policy)
    inspector.override_sandbox = {"sandbox_id": "invalid"}

    with pytest.raises(KubernetesRuntimeError, match="sandbox attestation"):
        runtime.create(unit, policy)


@pytest.mark.unit
def test_stage_and_proofs_revalidate_the_bound_node_fence(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, _, inspector = _runtime(unit, policy)
    runtime_unit = runtime.create(unit, policy)
    inspector.override_node = {"node_uid": UUID(int=98)}
    with pytest.raises(KubernetesRuntimeError, match="pre-staging attestation"):
        runtime.stage_request(runtime_unit, _request())
    with pytest.raises(KubernetesRuntimeError, match="exit is unconfirmed"):
        runtime.confirm_exit(runtime_unit)


@pytest.mark.unit
def test_stored_identity_can_recover_only_an_exact_known_runtime_unit(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, _, _ = _runtime(unit, policy)
    created = runtime.create(unit, policy)
    stored = SimpleNamespace(
        unit_id=created.unit_id,
        attempt_id=created.attempt_id,
        principal_id=created.principal_id,
        incarnation=created.incarnation,
    )
    runtime.hard_terminate(cast(RuntimeUnit, stored))
    with pytest.raises(KubernetesRuntimeError, match="runtime unit is invalid"):
        runtime.hard_terminate(
            cast(
                RuntimeUnit,
                SimpleNamespace(**{**vars(stored), "principal_id": UUID(int=1)}),
            )
        )


@pytest.mark.unit
@pytest.mark.parametrize("observed_pod", [None, []])
def test_attester_rejects_non_mapping_observed_pod(
    unit: ManagedUnit, policy: BrokerPolicy, observed_pod: object
) -> None:
    runtime, _, inspector = _runtime(unit, policy)
    inspector.override_sandbox = {"observed_pod": observed_pod}

    with pytest.raises(KubernetesRuntimeError, match="sandbox attestation"):
        runtime.create(unit, policy)


@pytest.mark.unit
def test_kubernetes_memory_budget_reserves_interpreter_margin(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    insufficient = replace(
        policy,
        limits=replace(
            policy.limits,
            memory_bytes=policy.limits.workspace_bytes
            + CONFIG.interpreter_memory_margin_bytes
            - 1,
        ),
    )
    runtime, _, _ = _runtime(
        replace(
            unit,
            policy_revision=insufficient.revision,
            policy_specification=policy_specification_evidence(insufficient),
        ),
        insufficient,
    )

    with pytest.raises(KubernetesRuntimeError, match="memory policy"):
        runtime.create(
            replace(
                unit,
                policy_revision=insufficient.revision,
                policy_specification=policy_specification_evidence(insufficient),
            ),
            insufficient,
        )


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
def test_hard_terminate_accepts_only_positive_exit_proof_when_exec_is_already_gone(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    runtime, control, _ = _runtime(unit, policy)
    runtime_unit = runtime.create(unit, policy)
    control.terminated = True
    control.fail_operation = "terminate"

    runtime.hard_terminate(runtime_unit)

    assert runtime.confirm_exit(runtime_unit) == runtime.confirm_exit(runtime_unit)


@pytest.mark.unit
@pytest.mark.parametrize(
    "container_ids",
    [(), ("9" * 64, "a" * 64), ("a" * 64,)],
)
def test_exit_rejects_missing_extra_or_substituted_container_identity(
    unit: ManagedUnit, policy: BrokerPolicy, container_ids: tuple[str, ...]
) -> None:
    runtime, control, inspector = _runtime(unit, policy)
    runtime_unit = runtime.create(unit, policy)
    control.terminated = True
    inspector.override_sandbox = {"container_ids": container_ids}

    with pytest.raises(KubernetesRuntimeError, match="exit is unconfirmed"):
        runtime.confirm_exit(runtime_unit)


@pytest.mark.unit
@pytest.mark.parametrize(
    "container_ids",
    [(), ("9" * 64, "a" * 64), ("a" * 64,)],
)
def test_created_terminal_recovery_rejects_changed_container_identity_set(
    unit: ManagedUnit, policy: BrokerPolicy, container_ids: tuple[str, ...]
) -> None:
    runtime, control, inspector = _runtime(unit, policy)
    runtime_unit = runtime.create(unit, policy)
    assert control.manifest is not None
    pod_contract = pod_contract_projection(control.manifest)
    contract = KubernetesAttestationContract(
        pod_contract,
        manifest_digest(pod_contract),
        policy,
        CONFIG.pool_name,
        CONFIG.node_fence_revision,
    )
    control.terminated = True
    inspector.override_sandbox = {"container_ids": container_ids}

    with pytest.raises(KubernetesRuntimeError, match="recovery attestation"):
        NodeAttestationEngine(inspector).recover(
            runtime_unit, contract, ManagedUnitState.CREATED
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "lifecycle_state",
    [ManagedUnitState.CREATED, ManagedUnitState.EXIT_CONFIRMED],
)
def test_terminal_recovery_rejects_observed_pod_identity_substitution(
    unit: ManagedUnit,
    policy: BrokerPolicy,
    lifecycle_state: ManagedUnitState,
) -> None:
    runtime, control, inspector = _runtime(unit, policy)
    created = runtime.create(unit, policy)
    assert control.manifest is not None
    pod_contract = pod_contract_projection(control.manifest)
    contract = KubernetesAttestationContract(
        pod_contract,
        manifest_digest(pod_contract),
        policy,
        CONFIG.pool_name,
        CONFIG.node_fence_revision,
    )
    control.terminated = True
    observed = deepcopy(control.manifest)
    metadata = cast(dict[str, object], observed["metadata"])
    metadata["uid"] = str(UUID(int=99))
    specification = cast(dict[str, object], observed["spec"])
    specification["nodeName"] = created.pod.node_name
    inspector.override_sandbox = {"observed_pod": observed}

    with pytest.raises(KubernetesRuntimeError, match="recovery attestation"):
        NodeAttestationEngine(inspector).recover(created, contract, lifecycle_state)


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
@pytest.mark.parametrize("terminal", [False, True])
def test_create_intent_discovery_recovers_original_contract_across_policy_rollover(
    unit: ManagedUnit, policy: BrokerPolicy, terminal: bool
) -> None:
    first, control, inspector = _runtime(unit, policy)
    expected = first.create(unit, policy)
    assert control.manifest is not None
    pod_contract = pod_contract_projection(control.manifest)
    contract = KubernetesAttestationContract(
        pod_contract,
        manifest_digest(pod_contract),
        policy,
        CONFIG.pool_name,
        CONFIG.node_fence_revision,
    )
    control.terminated = terminal
    rolled = replace(policy, revision="t74-rolled")
    restarted = KubernetesIsolationRuntime(
        image_repository="registry.example/markweave-reverse-attempt",
        policy=rolled,
        config=CONFIG,
        control_plane=control,
        node_attester=RecoveredIntentAttester(
            NodeAttestationEngine(inspector),
            expected.pod,
            expected.sandbox,
            contract,
        ),
    )

    assert restarted.discover(limit=1) == (expected,)


@pytest.mark.unit
def test_restart_discovery_neutralizes_unexpected_attester_failure(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    first, control, _ = _runtime(unit, policy)
    first.create(unit, policy)
    restarted = KubernetesIsolationRuntime(
        image_repository="registry.example/markweave-reverse-attempt",
        policy=policy,
        config=CONFIG,
        control_plane=control,
        node_attester=UnexpectedAttester(),
    )

    with pytest.raises(KubernetesRuntimeError, match="discovery failed") as raised:
        restarted.discover(limit=1)
    _assert_sensitive_marker_absent(raised.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    "factory",
    [
        lambda: KubernetesRuntimeConfig(
            "Bad", "attempt", "runtime", "pool", "fence", 1001, 1001, 67_108_864
        ),
        lambda: KubernetesRuntimeConfig(
            "valid", "attempt", "runtime", "pool", "fence", 0, 1001, 67_108_864
        ),
        lambda: KubernetesRuntimeConfig(
            "valid", "attempt", "runtime", "pool", "fence", 1001, 1001, 0
        ),
        lambda: KubernetesPodIdentity(
            "Bad",
            "pod",
            POD_UID,
            "node",
            UNIT_ID,
            ATTEMPT_ID,
            PRINCIPAL_ID,
            "policy",
            EVIDENCE,
        ),
        lambda: KubernetesSandboxIdentity(
            "bad", CGROUP, NODE_UID, EVIDENCE, (SANDBOX_ID,)
        ),
        pytest.param(
            lambda: KubernetesRuntimeUnit(
                UNIT_ID,
                RuntimeIncarnation(UNIT_ID, EVIDENCE),
                KubernetesPodIdentity(
                    "valid",
                    "pod",
                    POD_UID,
                    "node",
                    UNIT_ID,
                    ATTEMPT_ID,
                    PRINCIPAL_ID,
                    "policy",
                    EVIDENCE,
                ),
                KubernetesSandboxIdentity(
                    SANDBOX_ID, CGROUP, NODE_UID, EVIDENCE, (SANDBOX_ID,)
                ),
            ),
            id="incarnation_id_must_match_pod_uid",
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

    control.response = ReverseAttemptFailure(
        UUID(int=98), ReverseErrorCategory.PROTOCOL_ERROR
    )
    with pytest.raises(KubernetesRuntimeError, match="response is invalid"):
        runtime.try_collect_response(runtime_unit, ATTEMPT_ID)
    control.response = None

    unknown = replace(runtime_unit, unit_id=UUID(int=99))
    with pytest.raises(KubernetesRuntimeError, match="unknown"):
        runtime.hard_terminate(unknown)
    with pytest.raises(KubernetesRuntimeError, match="request"):
        runtime.stage_request(runtime_unit, cast(ReverseAttemptRequest, object()))
    with pytest.raises(KubernetesRuntimeError, match="response"):
        runtime.try_collect_response(runtime_unit, cast(UUID, "not-a-uuid"))
    with pytest.raises(KubernetesRuntimeError, match="runtime unit is invalid"):
        runtime.hard_terminate(cast(RuntimeUnit, object()))
    with pytest.raises(KubernetesRuntimeError, match="empty evidence is invalid"):
        runtime.confirm_removed(runtime_unit, cast(EvidenceDigest, object()))


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
    with pytest.raises(KubernetesRuntimeError, match="create failed") as raised:
        runtime.create(unit, policy)
    _assert_sensitive_marker_absent(raised.value)
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
        with pytest.raises(KubernetesRuntimeError, match=expected) as raised:
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
        _assert_sensitive_marker_absent(raised.value)
    control.fail_operation = None
    control.terminated = True
    empty = runtime.confirm_empty(runtime_unit)
    inspector.fail_operation = "removal"
    with pytest.raises(
        KubernetesRuntimeError, match="removal is unconfirmed"
    ) as raised:
        runtime.confirm_removed(runtime_unit, empty)
    _assert_sensitive_marker_absent(raised.value)


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


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["recover", "recover_create_intent", "acknowledge"])
def test_runtime_rejects_attester_missing_recovery_capability(
    unit: ManagedUnit, policy: BrokerPolicy, missing: str
) -> None:
    _, control, _ = _runtime(unit, policy)
    methods = {
        name: (lambda *args, **kwargs: None)
        for name in (
            "acknowledge",
            "adopt_create_intent",
            "bind",
            "confirm_empty",
            "confirm_exit",
            "confirm_removed",
            "recover",
            "recover_create_intent",
        )
        if name != missing
    }

    with pytest.raises(ValueError, match="runtime configuration"):
        KubernetesIsolationRuntime(
            image_repository="registry.example/reverse",
            policy=policy,
            config=CONFIG,
            control_plane=control,
            node_attester=cast(Any, SimpleNamespace(**methods)),
        )


def json_repr(value: object) -> str:
    return json.dumps(value, sort_keys=True)


def _assert_sensitive_marker_absent(error: BaseException) -> None:
    for candidate in (error, error.__cause__, error.__context__):
        assert SENSITIVE_MARKER not in str(candidate)
