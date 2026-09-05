from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from markweave.broker.fake_runtime import FakeIsolationRuntime, FakeRuntimeError
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    ManagedUnit,
    ManagedUnitState,
    RuntimeChannelLimits,
    RuntimeLimits,
    policy_specification_evidence,
)
from markweave.reversions.models import ReverseAttemptRequest, ReverseContentLimits
from tests.unit.broker.runtime_conformance import assert_lifecycle_conformance


@pytest.mark.unit
def test_fake_backend_satisfies_shared_runtime_contract() -> None:
    policy = BrokerPolicy(
        "conformance",
        f"sha256:{'a' * 64}",
        RuntimeLimits(1, 1, 1, 1, 1, 1),
        RuntimeChannelLimits(1, 1),
    )
    unit = ManagedUnit(
        UUID("11111111-1111-4111-8111-111111111111"),
        UUID("22222222-2222-4222-8222-222222222222"),
        AuthenticatedPrincipal(UUID("33333333-3333-4333-8333-333333333333")),
        1,
        policy.revision,
        policy_specification_evidence(policy),
        ManagedUnitState.CREATE_INTENT,
        1,
    )

    assert_lifecycle_conformance(FakeIsolationRuntime(), unit, policy)

    workspace_runtime = FakeIsolationRuntime()
    workspace_attempt_id = uuid4()
    workspace_unit = workspace_runtime.create(
        replace(unit, unit_id=uuid4(), attempt_id=workspace_attempt_id), policy
    )
    request = ReverseAttemptRequest(
        workspace_attempt_id,
        ".pdf",
        ReverseContentLimits(1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1),
        b"x",
    )
    workspace_runtime.stage_request(workspace_unit, request)
    assert (
        workspace_runtime.try_collect_response(workspace_unit, request.attempt_id)
        is None
    )
    with pytest.raises(FakeRuntimeError, match="workspace"):
        workspace_runtime.stage_request(workspace_unit, request)
