"""Shared HTTP response, cookie, and optimistic-concurrency helpers."""

from fastapi import Response
from fastapi.responses import HTMLResponse

from markweave.auth.policy_errors import (
    IdleSessionPolicyConflictError,
    IdleSessionPolicyPreconditionRequiredError,
)
from markweave.auth.service import AuthenticationService
from markweave.config import Settings
from markweave.jobs.models import ConversionJob
from markweave.templates.errors import (
    TemplateConflictError,
    TemplatePreconditionRequiredError,
)
from markweave.templates.models import TemplateIdentity
from markweave.web import WEB_SECURITY_HEADERS

from .schemas import ConversionResponse, TemplateResponse

CSRF_COOKIE_NAME = "__Host-md_converter_csrf"
_MAX_POLICY_REVISION_DIGITS = 19


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
        max_age=settings.session_absolute_seconds,
    )


def set_csrf_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        httponly=False,
        secure=True,
        samesite="lax",
        path="/",
        max_age=settings.session_absolute_seconds,
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        httponly=False,
        secure=True,
        samesite="lax",
        path="/",
    )


def web_response(content: str, *, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(
        content,
        status_code=status_code,
        headers={**WEB_SECURITY_HEADERS, "Cache-Control": "no-store"},
    )


def conversion_response(job: ConversionJob) -> ConversionResponse:
    return ConversionResponse.model_validate(job)


def template_response(
    template: TemplateIdentity, authentication: AuthenticationService
) -> TemplateResponse:
    user_repository = getattr(authentication, "users", None)
    owner = (
        user_repository.get_by_id(template.owner_id)
        if user_repository is not None
        else None
    )
    return TemplateResponse(
        id=template.id,
        owner_id=template.owner_id,
        name=template.name,
        description=template.description,
        status=template.status,
        revision=template.revision,
        current_version_id=template.current_version_id,
        owner_username=owner.username if owner is not None else "Unknown owner",
    )


def template_etag(template: TemplateIdentity) -> str:
    return f'"template-{template.id}-{template.revision}"'


def expected_revision(template_id, if_match: str | None) -> int:
    if if_match is None:
        raise TemplatePreconditionRequiredError
    prefix = f'"template-{template_id}-'
    if not if_match.startswith(prefix) or not if_match.endswith('"'):
        raise TemplateConflictError
    try:
        revision = int(if_match[len(prefix) : -1])
    except ValueError:
        raise TemplateConflictError from None
    if revision <= 0:
        raise TemplateConflictError
    return revision


def idle_session_policy_etag(revision: int) -> str:
    return f'"idle-session-policy-{revision}"'


def expected_idle_session_policy_revision(if_match: str | None) -> int:
    if if_match is None:
        raise IdleSessionPolicyPreconditionRequiredError
    prefix = '"idle-session-policy-'
    if not if_match.startswith(prefix) or not if_match.endswith('"'):
        raise IdleSessionPolicyConflictError
    candidate = if_match[len(prefix) : -1]
    if (
        len(candidate) > _MAX_POLICY_REVISION_DIGITS
        or not candidate.isascii()
        or not candidate.isdecimal()
        or (len(candidate) > 1 and candidate.startswith("0"))
    ):
        raise IdleSessionPolicyConflictError from None
    try:
        revision = int(candidate)
    except ValueError:
        raise IdleSessionPolicyConflictError from None
    if revision < 0:
        raise IdleSessionPolicyConflictError
    return revision
