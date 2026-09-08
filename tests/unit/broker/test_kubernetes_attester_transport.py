from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest

from markweave.broker import kubernetes_attester_transport as transport
from markweave.broker.kubernetes_attester import NodeAttestationEngine
from markweave.broker.kubernetes_attester_inventory import (
    AttesterLifecycleState,
    SQLiteNodeAttesterLedger,
)
from markweave.broker.kubernetes_attester_transport import (
    AttesterClientTlsConfig,
    AttesterReadinessPolicy,
    AttesterServerTlsConfig,
    AttesterTransportLimits,
    HttpsNodeAttesterClient,
    NodeAttesterService,
)
from markweave.broker.kubernetes_runtime import (
    KubernetesAttestationContract,
    KubernetesRuntimeError,
    KubernetesRuntimeUnit,
    manifest_digest,
    pod_contract_projection,
)
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    ManagedUnit,
    ManagedUnitState,
    RuntimeChannelLimits,
    RuntimeIncarnation,
    RuntimeLimits,
    policy_specification_evidence,
)
from tests.unit.broker.test_kubernetes_runtime import (
    ATTEMPT_ID,
    EVIDENCE,
    IMAGE_DIGEST,
    PRINCIPAL_ID,
    UNIT_ID,
    ControlPlaneDouble,
    InspectorDouble,
    _runtime,
)


@pytest.fixture
def policy() -> BrokerPolicy:
    return BrokerPolicy(
        "t74-transport",
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


def _binding(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> tuple[NodeAttesterService, dict[str, object], InspectorDouble, ControlPlaneDouble]:
    runtime, control, inspector = _runtime(unit, policy)
    created = runtime.create(unit, policy)
    assert control.manifest is not None
    pod = replace(created.pod, node_name="node-a")
    observed = deepcopy(control.manifest)
    metadata = cast(dict[str, object], observed["metadata"])
    specification = cast(dict[str, object], observed["spec"])
    metadata["uid"] = str(pod.pod_uid)
    specification["nodeName"] = pod.node_name
    inspector.override_sandbox = {"observed_pod": observed}
    contract_pod = pod_contract_projection(control.manifest)
    contract = KubernetesAttestationContract(
        contract_pod,
        manifest_digest(contract_pod),
        policy,
        "reverse",
        "fence-v1",
    )
    request = {
        "contract": {
            "fence_revision": contract.fence_revision,
            "manifest_digest": contract.manifest_digest.value,
            "pod_contract": contract.pod_contract,
            "policy": {
                "channel_limits": {
                    "max_input_bytes": policy.channel_limits.max_input_bytes,
                    "max_output_bytes": policy.channel_limits.max_output_bytes,
                },
                "image_digest": policy.image_digest,
                "limits": {
                    "cpu_period_micros": policy.limits.cpu_period_micros,
                    "cpu_quota_micros": policy.limits.cpu_quota_micros,
                    "memory_bytes": policy.limits.memory_bytes,
                    "pid_limit": policy.limits.pid_limit,
                    "wall_time_millis": policy.limits.wall_time_millis,
                    "workspace_bytes": policy.limits.workspace_bytes,
                },
                "revision": policy.revision,
            },
            "pool": contract.pool,
        },
        "operation": "bind",
        "pod": {
            "attempt_id": str(pod.attempt_id),
            "name": pod.name,
            "namespace": pod.namespace,
            "node_name": pod.node_name,
            "pod_uid": str(pod.pod_uid),
            "policy_revision": pod.policy_revision,
            "policy_specification": pod.policy_specification.value,
            "principal_id": str(pod.principal_id),
            "unit_id": str(pod.unit_id),
        },
        "protocol": "markweave-kubernetes-node-attester",
        "version": 1,
    }
    return (
        NodeAttesterService(
            NodeAttestationEngine(inspector),
            SQLiteNodeAttesterLedger(
                tmp_path / "attester.sqlite3", b"a" * 32, max_records=8
            ),
            node_name="node-a",
        ),
        request,
        inspector,
        control,
    )


def _call(
    service: NodeAttesterService, request: dict[str, object]
) -> dict[str, object]:
    return json.loads(service.handle(json.dumps(request).encode("ascii")))


@pytest.mark.unit
def test_service_keeps_sandbox_identity_server_side(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> None:
    service, bind_request, _, control = _binding(unit, policy, tmp_path)
    response = _call(service, bind_request)
    assert set(response) == {"outcome", "sandbox"}
    sandbox = cast(dict[str, object], response["sandbox"])
    assert set(sandbox) == {
        "cgroup_path",
        "container_ids",
        "node_fence",
        "node_uid",
        "sandbox_id",
    }

    control.terminated = True
    pod_uid = cast(dict[str, object], bind_request["pod"])["pod_uid"]
    exit_response = _call(
        service,
        {
            "operation": "confirm_exit",
            "pod_uid": pod_uid,
            "protocol": "markweave-kubernetes-node-attester",
            "version": 1,
        },
    )
    with pytest.raises(KubernetesRuntimeError, match="exit evidence"):
        _call(
            service,
            {
                "operation": "confirm_empty",
                "pod_uid": pod_uid,
                "prior_evidence": f"sha256:{'0' * 64}",
                "protocol": "markweave-kubernetes-node-attester",
                "version": 1,
            },
        )
    empty_response = _call(
        service,
        {
            "operation": "confirm_empty",
            "pod_uid": pod_uid,
            "prior_evidence": exit_response["evidence"],
            "protocol": "markweave-kubernetes-node-attester",
            "version": 1,
        },
    )
    with pytest.raises(KubernetesRuntimeError, match="empty evidence"):
        _call(
            service,
            {
                "operation": "confirm_removed",
                "pod_uid": pod_uid,
                "prior_evidence": f"sha256:{'0' * 64}",
                "protocol": "markweave-kubernetes-node-attester",
                "version": 1,
            },
        )
    removed = _call(
        service,
        {
            "operation": "confirm_removed",
            "pod_uid": pod_uid,
            "prior_evidence": empty_response["evidence"],
            "protocol": "markweave-kubernetes-node-attester",
            "version": 1,
        },
    )
    assert removed["outcome"] == "ok"
    assert (
        _call(
            service,
            {
                "operation": "confirm_removed",
                "pod_uid": pod_uid,
                "prior_evidence": empty_response["evidence"],
                "protocol": "markweave-kubernetes-node-attester",
                "version": 1,
            },
        )
        == removed
    )
    assert service._bound
    assert service._engine._contracts
    acknowledgement = {
        "operation": "acknowledge",
        "pod_uid": pod_uid,
        "protocol": "markweave-kubernetes-node-attester",
        "removed_evidence": removed["evidence"],
        "version": 1,
    }
    assert _call(service, acknowledgement)["acknowledged"] is True
    assert _call(service, acknowledgement)["acknowledged"] is True
    assert service._bound == {}
    assert service._engine._contracts == {}


@pytest.mark.unit
def test_service_rejects_node_substitution_and_caller_chosen_runtime_identity(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> None:
    service, request, _, _ = _binding(unit, policy, tmp_path)
    pod = cast(dict[str, object], request["pod"])
    pod["node_name"] = "node-b"
    with pytest.raises(KubernetesRuntimeError, match="node binding"):
        _call(service, request)

    request["operation"] = "confirm_exit"
    request["pod_uid"] = str(UUID(int=9))
    request.pop("contract")
    request.pop("pod")
    request["sandbox_id"] = "caller-chosen"
    with pytest.raises(KubernetesRuntimeError, match="request is invalid"):
        _call(service, request)


@pytest.mark.unit
def test_service_errors_are_content_free(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> None:
    service, request, inspector, _ = _binding(unit, policy, tmp_path)
    inspector.fail_operation = "node"
    with pytest.raises(
        KubernetesRuntimeError, match="node attestation failed"
    ) as raised:
        _call(service, request)
    assert "private-document-marker" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.unit
def test_transport_configuration_rejects_invalid_values() -> None:
    factories = (
        lambda: AttesterTransportLimits(0, 1, 1, 1),
        lambda: AttesterReadinessPolicy(0, 1),
        lambda: AttesterClientTlsConfig(
            0, Path("cert"), Path("key"), Path("ca"), EVIDENCE
        ),
        lambda: AttesterServerTlsConfig(
            "", 9443, Path("cert"), Path("key"), Path("ca"), EVIDENCE
        ),
        lambda: HttpsNodeAttesterClient(
            cast(AttesterClientTlsConfig, object()),
            AttesterTransportLimits(1, 1, 1, 1),
            AttesterReadinessPolicy(1, 1),
        ),
        lambda: NodeAttesterService(
            cast(NodeAttestationEngine, object()),
            cast(SQLiteNodeAttesterLedger, object()),
            node_name="node-a",
        ),
    )
    for factory in factories:
        with pytest.raises(ValueError):
            factory()


@pytest.mark.unit
def test_transport_configuration_rejects_non_finite_timing_values() -> None:
    for invalid in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="transport limits"):
            AttesterTransportLimits(1, 1, invalid, 1)
        with pytest.raises(ValueError, match="readiness policy"):
            AttesterReadinessPolicy(invalid, 0.1)
        with pytest.raises(ValueError, match="readiness policy"):
            AttesterReadinessPolicy(1.0, invalid)


@pytest.mark.unit
def test_service_rejects_changed_sandbox_binding(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> None:
    service, request, inspector, _ = _binding(unit, policy, tmp_path)
    _call(service, request)
    inspector.override_sandbox["sandbox_id"] = "a" * 64
    with pytest.raises(KubernetesRuntimeError, match="binding conflicts"):
        _call(service, request)


@pytest.mark.unit
def test_service_serializes_same_uid_state_transitions(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> None:
    service, request, _, _ = _binding(unit, policy, tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = tuple(executor.map(lambda _: _call(service, request), range(2)))
    assert responses[0] == responses[1]


@pytest.mark.unit
def test_service_rejects_malformed_closed_messages(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> None:
    service, request, _, _ = _binding(unit, policy, tmp_path)
    malformed_values = (
        b"not-json",
        json.dumps({"protocol": "wrong", "version": 1}).encode("ascii"),
        json.dumps(
            {
                "operation": "unknown",
                "protocol": "markweave-kubernetes-node-attester",
                "version": 1,
            }
        ).encode("ascii"),
    )
    for malformed in malformed_values:
        with pytest.raises(
            KubernetesRuntimeError, match=r"attester (message|request) is invalid"
        ):
            service.handle(malformed)

    request["extra"] = True
    with pytest.raises(KubernetesRuntimeError, match="request is invalid"):
        _call(service, request)


@pytest.mark.unit
def test_client_refuses_a_runtime_unit_that_it_did_not_bind() -> None:
    client = object.__new__(HttpsNodeAttesterClient)
    client._bound = {}
    with pytest.raises(KubernetesRuntimeError, match="binding is unknown"):
        client.confirm_exit(cast(KubernetesRuntimeUnit, object()))


@pytest.mark.unit
def test_service_exposes_only_explicit_not_ready_as_retryable(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> None:
    service, request, inspector, _ = _binding(unit, policy, tmp_path)
    inspector.fail_operation = "not_ready"
    assert _call(service, request) == {"outcome": "not_ready"}
    inspector.fail_operation = "sandbox"
    with pytest.raises(KubernetesRuntimeError, match="node attestation failed"):
        _call(service, request)


@pytest.mark.unit
def test_client_retries_only_not_ready_and_normalizes_malformed_binding(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> None:
    class ClientDouble(HttpsNodeAttesterClient):
        responses: list[dict[str, object]]

        def _exchange(self, node_name: str, request: object) -> dict[str, object]:
            del node_name, request
            return self.responses.pop(0)

    client = object.__new__(ClientDouble)
    client._readiness = AttesterReadinessPolicy(1, 0.1)
    client._monotonic = iter((0.0, 0.1)).__next__
    slept: list[float] = []
    client._sleep = slept.append
    client._bound = {}
    service, request, _, _ = _binding(unit, policy, tmp_path)
    pod = transport._pod(request["pod"])
    contract = transport._contract(request["contract"])
    good = _call(service, request)
    client.responses = [{"outcome": "not_ready"}, good]
    client.bind(pod, contract)
    assert slept == [0.1]

    client.responses = [{"outcome": "not_ready"}]
    client._monotonic = iter((0.0, 1.0)).__next__
    with pytest.raises(KubernetesRuntimeError, match="readiness timed out"):
        client.bind(replace(pod, pod_uid=UUID(int=98)), contract)

    client.responses = [{"outcome": "ok", "sandbox": {"sandbox_id": "invalid"}}]
    client._monotonic = lambda: 0.0
    with pytest.raises(KubernetesRuntimeError, match="response is invalid") as raised:
        client.bind(replace(pod, pod_uid=UUID(int=99)), contract)
    assert raised.value.__cause__ is None


@pytest.mark.unit
def test_recovery_clients_normalize_malformed_success_payloads(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> None:
    class ClientDouble(HttpsNodeAttesterClient):
        response: dict[str, object]

        def _exchange(
            self, node_name: str, request: Mapping[str, object]
        ) -> Mapping[str, object]:
            del node_name, request
            return self.response

    service, request, _, _ = _binding(unit, policy, tmp_path)
    bound = _call(service, request)
    pod = transport._pod(request["pod"])
    contract = transport._contract(request["contract"])
    sandbox = transport._sandbox(bound["sandbox"])
    runtime_unit = KubernetesRuntimeUnit(
        pod.unit_id,
        RuntimeIncarnation(pod.pod_uid, pod.policy_specification),
        pod,
        sandbox,
    )
    client = object.__new__(ClientDouble)
    client._bound = {}
    malformed_sandbox = dict(cast(dict[str, object], bound["sandbox"]))
    malformed_sandbox["node_uid"] = "private-malformed-uuid"
    malformed_contract = deepcopy(cast(dict[str, object], request["contract"]))
    malformed_policy = cast(dict[str, object], malformed_contract["policy"])
    malformed_limits = cast(dict[str, object], malformed_policy["limits"])
    malformed_limits["memory_bytes"] = True
    client.response = {
        "contract": malformed_contract,
        "outcome": "ok",
        "sandbox": bound["sandbox"],
    }
    with pytest.raises(KubernetesRuntimeError, match="response is invalid") as raised:
        client.recover_create_intent(pod, contract)
    assert raised.value.__cause__ is None

    client.response = {"outcome": "ok", "sandbox": malformed_sandbox}
    with pytest.raises(KubernetesRuntimeError, match="response is invalid") as raised:
        client.recover(runtime_unit, contract, ManagedUnitState.CREATED)
    assert "private-malformed-uuid" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.unit
def test_service_restarts_and_exactly_replays_every_durable_lifecycle_reply(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> None:
    service, request, inspector, control = _binding(unit, policy, tmp_path)
    bound = _call(service, request)
    pod_uid = cast(dict[str, object], request["pod"])["pod_uid"]

    service = NodeAttesterService(
        NodeAttestationEngine(inspector),
        SQLiteNodeAttesterLedger(
            tmp_path / "attester.sqlite3", b"a" * 32, max_records=8
        ),
        node_name="node-a",
    )
    assert _call(service, request) == bound
    assert _call(
        service,
        {
            "operation": "recover_create_intent",
            "pod": request["pod"],
            "proposed_contract": request["contract"],
            "protocol": "markweave-kubernetes-node-attester",
            "version": 1,
        },
    ) == {
        "contract": request["contract"],
        "outcome": "ok",
        "sandbox": bound["sandbox"],
    }

    control.terminated = True
    exit_request = {
        "operation": "confirm_exit",
        "pod_uid": pod_uid,
        "protocol": "markweave-kubernetes-node-attester",
        "version": 1,
    }
    exited = _call(service, exit_request)
    restarted = NodeAttesterService(
        NodeAttestationEngine(inspector),
        SQLiteNodeAttesterLedger(
            tmp_path / "attester.sqlite3", b"a" * 32, max_records=8
        ),
        node_name="node-a",
    )
    assert _call(restarted, exit_request) == exited
    assert (
        _call(
            restarted,
            {
                "contract": request["contract"],
                "lifecycle_state": ManagedUnitState.CREATED.value,
                "operation": "recover",
                "pod": request["pod"],
                "protocol": "markweave-kubernetes-node-attester",
                "sandbox": bound["sandbox"],
                "version": 1,
            },
        )
        == bound
    )

    empty_request = {
        **exit_request,
        "operation": "confirm_empty",
        "prior_evidence": exited["evidence"],
    }
    empty = _call(restarted, empty_request)
    restarted = NodeAttesterService(
        NodeAttestationEngine(inspector),
        SQLiteNodeAttesterLedger(
            tmp_path / "attester.sqlite3", b"a" * 32, max_records=8
        ),
        node_name="node-a",
    )
    assert _call(restarted, empty_request) == empty

    control.present = False
    removed_request = {
        **exit_request,
        "operation": "confirm_removed",
        "prior_evidence": empty["evidence"],
    }
    removed = _call(restarted, removed_request)
    restarted = NodeAttesterService(
        NodeAttestationEngine(inspector),
        SQLiteNodeAttesterLedger(
            tmp_path / "attester.sqlite3", b"a" * 32, max_records=8
        ),
        node_name="node-a",
    )
    assert (
        _call(
            restarted,
            {
                "contract": request["contract"],
                "lifecycle_state": ManagedUnitState.EMPTY_CONFIRMED.value,
                "operation": "recover",
                "pod": request["pod"],
                "protocol": "markweave-kubernetes-node-attester",
                "sandbox": bound["sandbox"],
                "version": 1,
            },
        )
        == bound
    )
    assert _call(restarted, removed_request) == removed
    assert _call(
        restarted,
        {
            "operation": "acknowledge",
            "pod_uid": pod_uid,
            "protocol": "markweave-kubernetes-node-attester",
            "removed_evidence": removed["evidence"],
            "version": 1,
        },
    ) == {"acknowledged": True, "outcome": "ok"}
    assert (
        SQLiteNodeAttesterLedger(
            tmp_path / "attester.sqlite3", b"a" * 32, max_records=8
        ).records()
        == ()
    )


@pytest.mark.unit
@pytest.mark.parametrize("terminal", [False, True])
def test_service_durably_adopts_prepared_create_before_bind_reply(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path, terminal: bool
) -> None:
    service, request, _, control = _binding(unit, policy, tmp_path)
    control.terminated = terminal

    adopted = _call(
        service,
        {
            "operation": "adopt_create_intent",
            "pod": request["pod"],
            "proposed_contract": request["contract"],
            "protocol": "markweave-kubernetes-node-attester",
            "version": 1,
        },
    )

    assert adopted["contract"] == request["contract"]
    assert adopted["outcome"] == "ok"
    records = SQLiteNodeAttesterLedger(
        tmp_path / "attester.sqlite3", b"a" * 32, max_records=8
    ).records()
    assert len(records) == 1
    assert records[0].state is AttesterLifecycleState.BOUND


@pytest.mark.unit
def test_service_recovers_bound_ledger_after_pod_exited_before_broker_commit(
    unit: ManagedUnit, policy: BrokerPolicy, tmp_path: Path
) -> None:
    service, request, inspector, control = _binding(unit, policy, tmp_path)
    bound = _call(service, request)
    control.terminated = True

    restarted = NodeAttesterService(
        NodeAttestationEngine(inspector),
        SQLiteNodeAttesterLedger(
            tmp_path / "attester.sqlite3", b"a" * 32, max_records=8
        ),
        node_name="node-a",
    )

    assert _call(
        restarted,
        {
            "operation": "recover_create_intent",
            "pod": request["pod"],
            "proposed_contract": request["contract"],
            "protocol": "markweave-kubernetes-node-attester",
            "version": 1,
        },
    ) == {
        "contract": request["contract"],
        "outcome": "ok",
        "sandbox": bound["sandbox"],
    }


@pytest.mark.unit
def test_wire_decoders_reject_ambiguous_or_unbounded_shapes() -> None:
    invalid_calls = (
        lambda: transport._decode(cast(bytes, "not-bytes")),
        lambda: transport._decode(b"[]"),
        lambda: transport._encode({"bad": object()}),
        lambda: transport._closed_mapping({"unexpected": True}, {"expected"}),
        lambda: transport._integers({"value": True}),
        lambda: transport._required_text({"value": ""}, "value"),
        lambda: transport._content_length(None),
        lambda: transport._content_length("0"),
    )
    for call in invalid_calls:
        with pytest.raises((KubernetesRuntimeError, ValueError)):
            call()

    class MissingCertificate:
        @staticmethod
        def getpeercert(*, binary_form: bool) -> None:
            assert binary_form is True

    with pytest.raises(KubernetesRuntimeError, match="peer identity"):
        transport._certificate_digest(MissingCertificate())
