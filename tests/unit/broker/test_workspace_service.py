from __future__ import annotations

from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.fake_runtime import FakeIsolationRuntime
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    ReplayPosition,
    RuntimeChannelLimits,
    RuntimeLimits,
)
from markweave.broker.service import IsolationBrokerService
from markweave.broker.workspace_protocol import (
    WorkspaceCollectRequest,
    WorkspaceFailureResponse,
    WorkspacePendingResponse,
    WorkspaceStageRequest,
    WorkspaceSuccessResponse,
)
from markweave.reversions.errors import ReverseErrorCategory
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptSuccess,
    ReverseContentLimits,
    ReverseOutputMode,
)

pytestmark = pytest.mark.unit

PRINCIPAL = AuthenticatedPrincipal(UUID("10000000-0000-4000-8000-000000000001"))
OTHER = AuthenticatedPrincipal(UUID("10000000-0000-4000-8000-000000000002"))
ATTEMPT = UUID("20000000-0000-4000-8000-000000000001")
UNIT = UUID("30000000-0000-4000-8000-000000000001")
POLICY = BrokerPolicy(
    "workspace-v1",
    "sha256:" + "a" * 64,
    RuntimeLimits(1, 1, 1, 1, 10000, 1),
    RuntimeChannelLimits(1000, 2000),
)
LIMITS = ReverseContentLimits(
    1000, 2000, 100, 10, 10, 100, 10, 5, 2, 100, 200, 500, 1000
)


def _service(tmp_path: Path) -> tuple[IsolationBrokerService, FakeIsolationRuntime]:
    runtime = FakeIsolationRuntime()
    service = IsolationBrokerService(
        SQLiteBrokerInventory(
            tmp_path / "broker.sqlite3",
            b"inventory-authentication-key-32b",
            max_records=4,
        ),
        runtime,
        POLICY,
        max_discovered_units=4,
        unit_id_factory=lambda: UNIT,
    )
    service.start()
    return service, runtime


def _stage(
    service: IsolationBrokerService, source: bytes = b"private"
) -> WorkspaceStageRequest:
    unit = service.status(PRINCIPAL, ATTEMPT, UNIT)
    assert unit.runtime_incarnation is not None
    return WorkspaceStageRequest(
        UUID("40000000-0000-4000-8000-000000000001"),
        8,
        ATTEMPT,
        UNIT,
        unit.create_sequence,
        ".docx",
        LIMITS,
        source,
    )


def _collect(
    stage: WorkspaceStageRequest,
    incarnation_id: UUID,
    *,
    request: UUID | None = None,
) -> WorkspaceCollectRequest:
    return WorkspaceCollectRequest(
        request or UUID("50000000-0000-4000-8000-000000000001"),
        9,
        stage.request_id,
        stage.sequence,
        stage.attempt_id,
        stage.unit_id,
        stage.create_sequence,
        incarnation_id,
    )


def test_exact_stage_replay_returns_receipt_without_second_runtime_copy(
    tmp_path: Path,
) -> None:
    service, runtime = _service(tmp_path)
    service.create(ReplayPosition(PRINCIPAL, 7), ATTEMPT)
    request = _stage(service)

    receipt = service.stage_workspace(PRINCIPAL, request)
    assert service.stage_workspace(PRINCIPAL, request) == receipt
    assert runtime.calls.count("stage_request:before") == 1

    changed = WorkspaceStageRequest(
        request.request_id,
        request.sequence,
        request.attempt_id,
        request.unit_id,
        request.create_sequence,
        request.extension,
        request.limits,
        b"different",
    )
    with pytest.raises(BrokerError) as caught:
        service.stage_workspace(PRINCIPAL, changed)
    assert caught.value.category is BrokerErrorCategory.REPLAY_REJECTED
    assert service.ready


def test_collect_is_receipt_bound_pending_failure_success_and_read_only(
    tmp_path: Path,
) -> None:
    service, runtime = _service(tmp_path)
    service.create(ReplayPosition(PRINCIPAL, 7), ATTEMPT)
    stage = _stage(service)
    receipt = service.stage_workspace(PRINCIPAL, stage)
    collect = _collect(stage, receipt.incarnation_id)

    assert service.collect_workspace(PRINCIPAL, collect) == WorkspacePendingResponse(
        collect.request_id, receipt
    )
    runtime.publish_response(
        UNIT, ReverseAttemptFailure(ATTEMPT, ReverseErrorCategory.MALFORMED)
    )
    assert service.collect_workspace(PRINCIPAL, collect) == WorkspaceFailureResponse(
        collect.request_id, receipt, ReverseErrorCategory.MALFORMED
    )
    runtime.publish_response(
        UNIT, ReverseAttemptSuccess(ATTEMPT, ReverseOutputMode.MARKDOWN, b"result")
    )
    assert service.collect_workspace(PRINCIPAL, collect) == WorkspaceSuccessResponse(
        collect.request_id, receipt, ReverseOutputMode.MARKDOWN, b"result"
    )
    assert service.collect_workspace(PRINCIPAL, collect) == WorkspaceSuccessResponse(
        collect.request_id, receipt, ReverseOutputMode.MARKDOWN, b"result"
    )


@pytest.mark.parametrize("substitution", ["owner", "create", "incarnation", "receipt"])
def test_workspace_substitution_is_rejected_without_runtime_copy(
    tmp_path: Path, substitution: str
) -> None:
    service, runtime = _service(tmp_path)
    service.create(ReplayPosition(PRINCIPAL, 7), ATTEMPT)
    stage = _stage(service)
    receipt = service.stage_workspace(PRINCIPAL, stage)
    collect = _collect(stage, receipt.incarnation_id)
    before = runtime.calls
    principal = PRINCIPAL
    changed = collect

    if substitution == "owner":
        principal = OTHER
    elif substitution == "create":
        changed = WorkspaceCollectRequest(
            collect.request_id,
            collect.sequence,
            collect.receipt_request_id,
            collect.stage_sequence,
            collect.attempt_id,
            collect.unit_id,
            collect.create_sequence + 1,
            collect.incarnation_id,
        )
    elif substitution == "incarnation":
        changed = WorkspaceCollectRequest(
            collect.request_id,
            collect.sequence,
            collect.receipt_request_id,
            collect.stage_sequence,
            collect.attempt_id,
            collect.unit_id,
            collect.create_sequence,
            UUID("60000000-0000-4000-8000-000000000001"),
        )
    else:
        changed = WorkspaceCollectRequest(
            collect.request_id,
            collect.sequence,
            UUID(int=9),
            collect.stage_sequence,
            collect.attempt_id,
            collect.unit_id,
            collect.create_sequence,
            collect.incarnation_id,
        )
    with pytest.raises(BrokerError) as caught:
        service.collect_workspace(principal, changed)
    assert caught.value.category is BrokerErrorCategory.PROTOCOL_ERROR
    assert runtime.calls == before
    assert receipt.attempt_id == ATTEMPT


def test_runtime_stage_and_collect_failure_fence_readiness(tmp_path: Path) -> None:
    service, runtime = _service(tmp_path)
    service.create(ReplayPosition(PRINCIPAL, 7), ATTEMPT)
    stage = _stage(service)
    runtime.inject_fault("stage_request")
    with pytest.raises(BrokerError) as caught:
        service.stage_workspace(PRINCIPAL, stage)
    assert caught.value.category is BrokerErrorCategory.RUNTIME_FAILURE
    assert not service.ready

    service.runtime_reconnected()
    # Reconciliation terminates the orphan, so a workspace request cannot resume.
    with pytest.raises(BrokerError):
        service.stage_workspace(PRINCIPAL, stage)


def test_workspace_rejects_invalid_models_and_wrong_runtime_response(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    service, runtime = _service(tmp_path)
    service.create(ReplayPosition(PRINCIPAL, 7), ATTEMPT)
    stage = _stage(service)
    receipt = service.stage_workspace(PRINCIPAL, stage)
    collect = _collect(stage, receipt.incarnation_id)

    with pytest.raises(BrokerError):
        service.stage_workspace(PRINCIPAL, cast(Any, object()))
    with pytest.raises(BrokerError):
        service.collect_workspace(PRINCIPAL, cast(Any, object()))
    mocker.patch.object(
        runtime,
        "try_collect_response",
        return_value=ReverseAttemptFailure(
            UUID("70000000-0000-4000-8000-000000000001"),
            ReverseErrorCategory.MALFORMED,
        ),
    )
    with pytest.raises(BrokerError) as caught:
        service.collect_workspace(PRINCIPAL, collect)
    assert caught.value.category is BrokerErrorCategory.RUNTIME_FAILURE
    assert not service.ready


def test_workspace_ledger_is_released_only_after_durable_acknowledgement(
    tmp_path: Path,
) -> None:
    service, _ = _service(tmp_path)
    service.create(ReplayPosition(PRINCIPAL, 7), ATTEMPT)
    stage = _stage(service)
    service.stage_workspace(PRINCIPAL, stage)
    proof = service.terminate(PRINCIPAL, ATTEMPT, UNIT)
    assert UNIT in service._staged_workspaces
    assert service.acknowledge(PRINCIPAL, ATTEMPT, UNIT, proof.proof_id)
    assert UNIT not in service._staged_workspaces
