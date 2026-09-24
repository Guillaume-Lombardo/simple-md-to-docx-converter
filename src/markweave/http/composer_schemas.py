"""Public Composer connection contracts without readable credential fields."""

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from markweave.presentations.models import PresentationDialect


class ComposerAvailabilityState(StrEnum):
    UNCONFIGURED = "unconfigured"
    DISABLED = "disabled"
    UNAUTHORIZED = "unauthorized"
    READY = "ready"
    OUTAGE = "outage"


class ConnectionScope(StrEnum):
    INSTANCE = "instance"
    PERSONAL = "personal"


class ConnectionIdentityMode(StrEnum):
    SHARED = "shared"
    INDIVIDUAL = "individual"


class ComposerCapabilitiesResponse(BaseModel):
    status: ComposerAvailabilityState
    status_message: str | None
    personal_connections_allowed: bool
    instance_connections_manageable: bool
    maximum_upload_bytes: int | None
    maximum_credential_bytes: int | None
    maximum_model_request_bytes: int | None
    maximum_output_tokens: int | None


class ComposerAdminPolicyResponse(BaseModel):
    mode: str
    enabled: bool
    allowed_destinations: tuple[str, ...]
    allowed_networks: tuple[str, ...]
    editable_destinations: bool
    etag: str


class ComposerAdminPolicyUpdateRequest(BaseModel):
    enabled: bool
    allowed_destinations: tuple[str, ...] = Field(max_length=100)
    allowed_networks: tuple[str, ...] = Field(max_length=100)


class ComposerDestinationResolveRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2048)


class ComposerDestinationResolveResponse(BaseModel):
    destination: str
    addresses: tuple[str, ...]


class ConnectionResponse(BaseModel):
    id: UUID
    name: str
    scope: ConnectionScope
    identity_mode: ConnectionIdentityMode
    endpoint: str
    enabled: bool
    selected_model: str | None
    permitted_models: tuple[str, ...]
    allowed_user_ids: tuple[UUID, ...]
    credential_present: bool
    client_certificate_present: bool
    internal_ca_present: bool
    authorized: bool
    status: ComposerAvailabilityState
    status_message: str | None
    etag: str


class ConnectionListResponse(BaseModel):
    connections: tuple[ConnectionResponse, ...]
    limit: int
    offset: int


class ConnectionCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scope: ConnectionScope
    identity_mode: ConnectionIdentityMode
    endpoint: str = Field(min_length=1, max_length=2048)
    enabled: bool = False
    selected_model: str | None = Field(default=None, max_length=256)
    permitted_models: tuple[str, ...] = ()
    allowed_user_ids: tuple[UUID, ...] = ()


class ConnectionUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    endpoint: str | None = Field(default=None, min_length=1, max_length=2048)
    enabled: bool | None = None
    selected_model: str | None = Field(default=None, max_length=256)
    permitted_models: tuple[str, ...] | None = None
    allowed_user_ids: tuple[UUID, ...] | None = None


class CredentialWriteRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    api_key: SecretStr | None = None
    client_certificate: SecretStr | None = None
    client_private_key: SecretStr | None = None
    internal_ca: SecretStr | None = None
    revoke: bool = False

    @model_validator(mode="after")
    def validate_action(self) -> CredentialWriteRequest:
        has_secret = any(
            value is not None
            for value in (
                self.api_key,
                self.client_certificate,
                self.client_private_key,
                self.internal_ca,
            )
        )
        if self.revoke == has_secret:
            raise ValueError("Supply credentials or request revocation")
        return self


class ConnectionModelsResponse(BaseModel):
    models: tuple[str, ...]


class ConnectionTestRequest(BaseModel):
    model: str | None = Field(default=None, max_length=256)


class ConnectionTestResponse(BaseModel):
    status: ComposerAvailabilityState
    status_message: str | None


class PersonalPermissionResponse(BaseModel):
    user_id: UUID
    username: str
    allowed: bool
    etag: str


class PersonalPermissionListResponse(BaseModel):
    permissions: tuple[PersonalPermissionResponse, ...]
    limit: int
    offset: int


class PersonalPermissionUpdateRequest(BaseModel):
    allowed: bool


class ComposerDraftResponse(BaseModel):
    id: UUID
    title: str
    content: str
    version: int
    current_revision_id: UUID | None
    source_kind: str
    source_media_type: str
    created_at: datetime
    updated_at: datetime
    etag: str


class ComposerDraftSummaryResponse(BaseModel):
    id: UUID
    title: str
    version: int
    current_revision_id: UUID | None
    source_kind: str
    source_media_type: str
    updated_at: datetime
    etag: str


class ComposerDraftListResponse(BaseModel):
    drafts: tuple[ComposerDraftSummaryResponse, ...]
    limit: int
    offset: int


class ComposerDraftUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    content: str


class ComposerHandoffRequest(BaseModel):
    title: str | None = None


class ComposerMessageCreateRequest(BaseModel):
    content: str = Field(min_length=1)


class ComposerMessageResponse(BaseModel):
    id: UUID
    draft_id: UUID
    role: str
    content: str
    created_at: datetime


class ComposerMessageListResponse(BaseModel):
    messages: tuple[ComposerMessageResponse, ...]
    limit: int
    offset: int


class ComposerProposalDecision(StrEnum):
    ACCEPTED = "accepted"
    EDITED = "edited"
    REJECTED = "rejected"


class ComposerProposalDecisionRequest(BaseModel):
    state: ComposerProposalDecision
    decided_value: str | None = None


class ComposerProposalResponse(BaseModel):
    id: UUID
    draft_id: UUID
    base_version: int
    state: str
    proposed_value: str
    decided_value: str | None
    provenance: str
    created_at: datetime
    decided_at: datetime | None
    decided_by: UUID | None


class ComposerProposalListResponse(BaseModel):
    proposals: tuple[ComposerProposalResponse, ...]
    limit: int
    offset: int


class ComposerModelStepCreateRequest(BaseModel):
    """Exact user-approved text for one bounded model proposal attempt."""

    model_config = ConfigDict(hide_input_in_errors=True)

    connection_id: UUID
    approved_endpoint: str = Field(min_length=1)
    approved_model: str = Field(min_length=1)
    content: str = Field(min_length=1)
    max_output_tokens: int = Field(gt=0)
    intent: Literal["proposal", "question"] = "proposal"
    answered_question_id: UUID | None = None


class ComposerModelStepResponse(BaseModel):
    id: UUID
    draft_id: UUID
    connection_id: UUID
    model_identity: str
    base_version: int
    status: str
    proposal_id: UUID | None
    intent: Literal["proposal", "question"]
    answered_question_id: UUID | None
    question_id: UUID | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class ComposerQuestionResponse(BaseModel):
    id: UUID
    draft_id: UUID
    model_step_id: UUID
    base_version: int
    state: Literal["pending", "answered"]
    text: str
    answer_message_id: UUID | None
    answer_content: str | None
    created_at: datetime
    answered_at: datetime | None


class ComposerQuestionListResponse(BaseModel):
    questions: tuple[ComposerQuestionResponse, ...]
    limit: int
    offset: int


class ComposerQuestionAnswerRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    content: str = Field(min_length=1)


class ComposerGenerationCreateRequest(BaseModel):
    output: Literal["docx", "pdf", "pptx"]
    template_id: UUID | None = None
    template_version_id: UUID | None = None
    presentation_dialect: PresentationDialect | None = None
    slide_level: int | None = Field(default=None, ge=1, le=6)

    @model_validator(mode="after")
    def validate_options(self) -> ComposerGenerationCreateRequest:
        if (self.template_id is None) is not (self.template_version_id is None):
            raise ValueError("Template ID and version must be supplied together")
        if self.output != "pptx" and (
            self.presentation_dialect is not None or self.slide_level is not None
        ):
            raise ValueError("Presentation options require PowerPoint output")
        return self


class ComposerGenerationResponse(BaseModel):
    id: UUID
    draft_id: UUID
    source_revision_id: UUID
    job_id: UUID | None
    status: str
    output: Literal["docx", "pdf", "pptx"]
    template_id: UUID | None
    template_version_id: UUID | None
    presentation_dialect: PresentationDialect | None
    slide_level: int | None
    result_revision_id: UUID | None
    publishable: bool
    created_at: datetime


class ComposerGenerationListResponse(BaseModel):
    generations: tuple[ComposerGenerationResponse, ...]
    limit: int
    offset: int


class ComposerArtifactResponse(BaseModel):
    kind: str
    sha256: str
    size: int
    media_type: str


class ComposerRevisionResponse(BaseModel):
    id: UUID
    draft_id: UUID
    number: int
    operation: str
    provenance: str
    source_sha256: str
    template_reference: str | None
    approved_values: str
    render_options: str
    model_identity: str | None
    artifacts: tuple[ComposerArtifactResponse, ...]
    restored_from_revision_id: UUID | None
    created_at: datetime


class ComposerRevisionSummaryResponse(BaseModel):
    id: UUID
    draft_id: UUID
    number: int
    operation: str
    provenance: str
    model_identity: str | None
    restored_from_revision_id: UUID | None
    created_at: datetime


class ComposerRevisionListResponse(BaseModel):
    revisions: tuple[ComposerRevisionSummaryResponse, ...]
    limit: int
    offset: int


class ComposerRevisionDiffChange(BaseModel):
    kind: str
    before_start: int
    before_end: int
    after_start: int
    after_end: int
    before_text: str
    after_text: str


class ComposerRevisionDiffResponse(BaseModel):
    from_revision_id: UUID
    to_revision_id: UUID
    status: str
    reason: str | None
    changes: tuple[ComposerRevisionDiffChange, ...]
    scope: Literal["artifact", "approved_markdown"] = "artifact"
    metadata_changes: tuple[str, ...] = ()
