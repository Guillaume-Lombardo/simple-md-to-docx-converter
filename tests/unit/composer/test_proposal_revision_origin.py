"""Bind a published model proposal to its original completed model identity."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pytest_mock import MockerFixture

from markweave.composer.drafts import ComposerProposal, ProposalState
from markweave.composer.revisions import ComposerConflictError
from markweave.http.composer_errors import ComposerUnavailableError
from markweave.http.routers.composer_revisions import _proposal_model_identity

pytestmark = pytest.mark.unit


def test_model_proposal_publication_freezes_completed_step_model(
    mocker: MockerFixture,
) -> None:
    owner_id, draft_id, step_id, proposal_id = (UUID(int=n) for n in range(1, 5))
    proposal = ComposerProposal(
        proposal_id,
        draft_id,
        1,
        ProposalState.EDITED,
        "suggestion",
        "correction",
        f"model-step:{step_id}",
        datetime.now(UTC),
        datetime.now(UTC),
        owner_id,
    )
    dependencies = mocker.Mock()
    step = dependencies.components.composer_model_step_repository.get_model_step.return_value
    step.status = "completed"
    step.proposal_id = proposal_id
    step.model_identity = "approved-model"
    assert (
        _proposal_model_identity(dependencies, owner_id, draft_id, proposal)
        == "approved-model"
    )
    dependencies.components.composer_model_step_repository.get_model_step.assert_called_once_with(
        owner_id, draft_id, step_id
    )
    step.proposal_id = UUID(int=5)
    with pytest.raises(ComposerConflictError):
        _proposal_model_identity(dependencies, owner_id, draft_id, proposal)
    step.proposal_id = proposal_id
    step.status = "cancelled"
    with pytest.raises(ComposerConflictError):
        _proposal_model_identity(dependencies, owner_id, draft_id, proposal)
    dependencies.components.composer_model_step_repository = None
    with pytest.raises(ComposerUnavailableError):
        _proposal_model_identity(dependencies, owner_id, draft_id, proposal)


def test_human_proposal_has_no_model_identity(mocker: MockerFixture) -> None:
    owner_id, draft_id, proposal_id = (UUID(int=n) for n in range(1, 4))
    proposal = ComposerProposal(
        proposal_id,
        draft_id,
        1,
        ProposalState.ACCEPTED,
        "suggestion",
        "suggestion",
        "manual-suggestion",
        datetime.now(UTC),
        datetime.now(UTC),
        owner_id,
    )
    dependencies = mocker.Mock()
    assert _proposal_model_identity(dependencies, owner_id, draft_id, proposal) is None
    dependencies.components.composer_model_step_repository.get_model_step.assert_not_called()
    invalid = ComposerProposal(
        proposal.id,
        proposal.draft_id,
        proposal.base_version,
        proposal.state,
        proposal.proposed_value,
        proposal.decided_value,
        "model-step:not-a-uuid",
        proposal.created_at,
        proposal.decided_at,
        proposal.decided_by,
    )
    with pytest.raises(ComposerConflictError):
        _proposal_model_identity(dependencies, owner_id, draft_id, invalid)
