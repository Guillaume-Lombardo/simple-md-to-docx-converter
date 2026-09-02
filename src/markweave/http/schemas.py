"""Stable request and response schemas for the HTTP contract."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from markweave.auth.models import Role
from markweave.jobs.models import JobOutput, TemplateMode
from markweave.templates.models import TemplateSelectionSource, TemplateStatus


class LoginRequest(BaseModel):
    """JSON local-login request."""

    username: str
    password: str


class UserCreateRequest(BaseModel):
    """Administrator local-account creation request."""

    username: str
    password: str
    password_change_required: bool = False


class ActiveUpdateRequest(BaseModel):
    """Administrator account status request."""

    active: bool


class PasswordResetRequest(BaseModel):
    """Administrator password reset request."""

    password: str
    password_change_required: bool = False


class PasswordChangeRequirementRequest(BaseModel):
    """Administrator password-renewal requirement request."""

    required: bool


class PasswordChangeRequest(BaseModel):
    """Authenticated self-service password renewal request."""

    password: str
    confirmation: str


class IdleSessionPolicyUpdateRequest(BaseModel):
    """Atomic administrator update for both role-specific durations."""

    user_idle_minutes: Annotated[int, Field(strict=True, ge=5, le=300)]
    admin_idle_minutes: Annotated[int, Field(strict=True, ge=5, le=60)]


class IdleSessionPolicyDurationBoundsResponse(BaseModel):
    """Authoritative whole-minute bounds and default for one session role."""

    minimum_minutes: Annotated[int, Field(strict=True, ge=1)]
    default_minutes: Annotated[int, Field(strict=True, ge=1)]
    maximum_minutes: Annotated[int, Field(strict=True, ge=1)]


class IdleSessionPolicyResponse(BaseModel):
    """Effective singleton policy and optimistic-concurrency revision."""

    model_config = ConfigDict(from_attributes=True)

    user_idle_minutes: int
    admin_idle_minutes: int
    revision: int
    absolute_lifetime_seconds: Annotated[int, Field(strict=True, gt=0)]
    user_idle_minutes_bounds: IdleSessionPolicyDurationBoundsResponse
    admin_idle_minutes_bounds: IdleSessionPolicyDurationBoundsResponse
    idle_minutes_granularity: Annotated[int, Field(strict=True, ge=1)]


class UserResponse(BaseModel):
    """Public local-account representation without password material."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    username: str
    role: Role
    active: bool
    password_change_required: bool
    effective_idle_minutes: int | None = Field(
        default=None,
        description=(
            "Current server-enforced inactivity duration. Present on login and "
            "session inspection responses."
        ),
        exclude_if=lambda value: value is None,
    )


class LoginResponse(BaseModel):
    """Successful login response containing the session-bound CSRF token once."""

    user: UserResponse
    csrf_token: str = Field(
        description=(
            "Send as X-CSRF-Token for authenticated mutations. Browser clients also "
            "receive the same value in the Secure, SameSite=Lax "
            "__Host-md_converter_csrf cookie."
        )
    )


class ConversionResponse(BaseModel):
    """Safe persistent conversion snapshot."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    template_mode: TemplateMode
    template_id: UUID | None
    template_version_id: UUID | None
    output: JobOutput
    component_versions: tuple[tuple[str, str], ...]
    correlation_id: str
    state: str
    step: str
    progress: int
    created_at: datetime
    updated_at: datetime
    attempt: int
    cancel_requested: bool
    error_code: str | None
    error_message: str | None
    expires_at: datetime | None


class ConversionPageResponse(BaseModel):
    """Paginated owner conversion response."""

    items: tuple[ConversionResponse, ...]
    total: int
    offset: int
    limit: int


class TemplateResponse(BaseModel):
    """Visible template identity and optimistic-concurrency revision."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    owner_id: UUID
    name: str
    description: str
    status: TemplateStatus
    revision: int
    current_version_id: UUID | None
    owner_username: str


class ConversionOptionsResponse(BaseModel):
    """Authoritative limits and immutable template selection for conversion."""

    conversion_upload_max_bytes: Annotated[int, Field(strict=True, gt=0)]
    resolved_template: TemplateResponse | None
    template_version_id: UUID | None
    selection_source: TemplateSelectionSource

    @model_validator(mode="after")
    def validate_selection(self) -> ConversionOptionsResponse:
        has_template = self.resolved_template is not None
        has_version = self.template_version_id is not None
        is_default = self.selection_source is TemplateSelectionSource.PANDOC_DEFAULT
        if has_template != has_version or is_default == has_template:
            raise ValueError("Template selection fields are inconsistent")
        if (
            self.resolved_template is not None
            and self.resolved_template.current_version_id != self.template_version_id
        ):
            raise ValueError("Template version fields are inconsistent")
        return self


class TemplateAdministrationContextResponse(BaseModel):
    """Authoritative template selection identifiers and upload limit."""

    preferred_template_id: UUID | None
    system_fallback_template_id: UUID | None
    template_max_archive_bytes: Annotated[int, Field(strict=True, gt=0)]


class TemplatePageResponse(BaseModel):
    items: tuple[TemplateResponse, ...]
    total: int
    offset: int
    limit: int


class TemplateVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    template_id: UUID
    number: int
    sha256: str
    size: int
    created_at: datetime
    created_by: UUID
    restored_from_version_id: UUID | None
    declared_fonts: tuple[str, ...]
    resolved_fonts: tuple[tuple[str, str], ...]
    validation_trace: tuple[str, ...]


class AuditRecordResponse(BaseModel):
    """Administrator-visible content-free immutable audit evidence."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID
    owner_id: UUID
    operation: str
    target_id: UUID
    target_type: str
    target_version: str | None
    version_id: UUID | None
    administrator_intervention: bool
    created_at: datetime
    old_user_idle_minutes: int | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    old_admin_idle_minutes: int | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    new_user_idle_minutes: int | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    new_admin_idle_minutes: int | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class TemplateMetadataRequest(BaseModel):
    name: str
    description: str


class ErrorDetail(BaseModel):
    """Stable machine-readable functional error detail."""

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Stable error envelope used for every expected HTTP failure."""

    error: ErrorDetail
