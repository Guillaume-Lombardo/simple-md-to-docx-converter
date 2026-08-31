"""Per-application authentication, authorization, and runtime dependencies."""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, Request

from markweave.auth.errors import LOGIN_ORIGIN_INVALID
from markweave.auth.models import User
from markweave.auth.service import AuthenticationService
from markweave.config import Settings
from markweave.templates.service import TemplateService

from .components import AppComponents


@dataclass(frozen=True, slots=True)
class HttpDependencies:
    """Request dependencies bound to one application composition."""

    settings: Settings
    components: AppComponents
    authentication: AuthenticationService
    public_origin: str | None

    def session_token(self, request: Request) -> str | None:
        return request.cookies.get(self.settings.session_cookie_name)

    def authenticated_user(self, request: Request) -> User:
        return self.authentication.authenticate(
            self.session_token(request), allow_password_change=True
        )

    def current_user(self, request: Request) -> User:
        return self.authentication.authenticate(self.session_token(request))

    def enforce_login_origin(self, request: Request) -> None:
        if self.settings.insecure_evaluation_mode:
            return
        origin = request.headers.get("Origin")
        if origin is None:
            return
        expected = self.public_origin or str(request.base_url).rstrip("/").casefold()
        if origin.rstrip("/").casefold() != expected:
            raise LOGIN_ORIGIN_INVALID.new()

    def mutation_actor(
        self,
        request: Request,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> User:
        token = self.session_token(request)
        user = self.authentication.authenticate(token)
        self.authentication.validate_csrf(token, csrf_token)
        return user

    def password_change_actor(
        self,
        request: Request,
        csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
    ) -> User:
        token = self.session_token(request)
        user = self.authentication.authenticate(token, allow_password_change=True)
        self.authentication.validate_csrf(token, csrf_token)
        return user

    def template_runtime(self) -> TemplateService:
        if self.components.templates is None:
            raise RuntimeError("Template API runtime is not configured")
        return self.components.templates
