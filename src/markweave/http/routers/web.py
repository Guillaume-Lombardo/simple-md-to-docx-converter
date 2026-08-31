"""Server-rendered pages and static browser assets."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse

from markweave.auth.errors import PASSWORD_CHANGE_REQUIRED, AuthenticationError
from markweave.http.dependencies import HttpDependencies
from markweave.http.responses import (
    CSRF_COOKIE_NAME,
    clear_session_cookie,
    set_csrf_cookie,
    set_session_cookie,
    web_response,
)
from markweave.web import (
    WEB_SECURITY_HEADERS,
    render_conversion_page,
    render_login_page,
    render_password_change_page,
    render_templates_page,
)

STATIC_DIRECTORY = Path(__file__).parents[2] / "static"


def build_router(  # noqa: PLR0915 - route declarations are intentionally grouped
    dependencies: HttpDependencies,
) -> APIRouter:
    """Build browser routes bound to one application."""

    router = APIRouter()
    auth = dependencies.authentication
    settings = dependencies.settings
    components = dependencies.components

    @router.get("/", include_in_schema=False)
    def browser_root() -> RedirectResponse:
        return RedirectResponse("/convert", status_code=303)

    @router.get("/static/conversion.css", include_in_schema=False)
    def conversion_stylesheet() -> Response:
        return Response(
            (STATIC_DIRECTORY / "conversion.css").read_bytes(),
            media_type="text/css",
            headers={**WEB_SECURITY_HEADERS, "Cache-Control": "public, max-age=3600"},
        )

    @router.get("/static/conversion.js", include_in_schema=False)
    def conversion_javascript() -> Response:
        return Response(
            (STATIC_DIRECTORY / "conversion.js").read_bytes(),
            media_type="text/javascript",
            headers={**WEB_SECURITY_HEADERS, "Cache-Control": "public, max-age=3600"},
        )

    @router.get("/static/administration.css", include_in_schema=False)
    def administration_stylesheet() -> Response:
        return Response(
            (STATIC_DIRECTORY / "administration.css").read_bytes(),
            media_type="text/css",
            headers={**WEB_SECURITY_HEADERS, "Cache-Control": "public, max-age=3600"},
        )

    @router.get("/static/administration.js", include_in_schema=False)
    def administration_javascript() -> Response:
        return Response(
            (STATIC_DIRECTORY / "administration.js").read_bytes(),
            media_type="text/javascript",
            headers={**WEB_SECURITY_HEADERS, "Cache-Control": "public, max-age=3600"},
        )

    @router.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page() -> HTMLResponse:
        return web_response(render_login_page())

    @router.get(
        "/change-password", response_class=HTMLResponse, include_in_schema=False
    )
    def password_change_page(request: Request) -> Response:
        try:
            actor = dependencies.authenticated_user(request)
        except AuthenticationError:
            return RedirectResponse("/login", status_code=303)
        if not actor.password_change_required:
            return RedirectResponse("/convert", status_code=303)
        csrf_token = request.cookies.get(CSRF_COOKIE_NAME)
        if not csrf_token:
            auth.logout(dependencies.session_token(request))
            response = RedirectResponse("/login", status_code=303)
            clear_session_cookie(response, settings)
            return response
        return web_response(render_password_change_page(actor, csrf_token))

    @router.post("/change-password", include_in_schema=False)
    def browser_password_change(
        request: Request,
        password: Annotated[str, Form()],
        confirmation: Annotated[str, Form()],
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        token = dependencies.session_token(request)
        try:
            actor = auth.authenticate(token, allow_password_change=True)
            auth.validate_csrf(token, csrf_token)
            auth.change_password(actor, password, confirmation)
        except AuthenticationError as error:
            if error.status_code == status.HTTP_401_UNAUTHORIZED:
                return RedirectResponse("/login", status_code=303)
            return web_response(
                render_password_change_page(actor, csrf_token, invalid=True),
                status_code=error.status_code,
            )
        response = RedirectResponse("/login", status_code=303)
        clear_session_cookie(response, settings)
        return response

    @router.post("/logout", include_in_schema=False)
    def browser_logout(
        request: Request,
        csrf_token: Annotated[str, Form()],
    ) -> Response:
        token = dependencies.session_token(request)
        auth.authenticate(token, allow_password_change=True)
        auth.validate_csrf(token, csrf_token)
        auth.logout(token)
        response = RedirectResponse("/login", status_code=303)
        clear_session_cookie(response, settings)
        return response

    @router.get("/convert", response_class=HTMLResponse, include_in_schema=False)
    def conversion_page(request: Request) -> Response:
        try:
            actor = dependencies.current_user(request)
        except AuthenticationError as error:
            if error.code == PASSWORD_CHANGE_REQUIRED.code:
                return RedirectResponse("/change-password", status_code=303)
            return RedirectResponse("/login", status_code=303)
        runtime = dependencies.template_runtime()
        selected = runtime.resolve(actor)
        label = (
            runtime.selection_label(actor, selected) if selected is not None else None
        )
        recent = components.jobs.list_owner(actor.id, offset=0, limit=10)
        return web_response(
            render_conversion_page(
                actor,
                selected,
                label,
                recent.items,
                maximum_upload_bytes=settings.conversion_upload_max_bytes,
            )
        )

    @router.get("/templates", response_class=HTMLResponse, include_in_schema=False)
    def templates_page(request: Request) -> Response:
        try:
            actor = dependencies.current_user(request)
        except AuthenticationError as error:
            if error.code == PASSWORD_CHANGE_REQUIRED.code:
                return RedirectResponse("/change-password", status_code=303)
            return RedirectResponse("/login", status_code=303)
        runtime = dependencies.template_runtime()
        selected = runtime.resolve(actor)
        label = (
            runtime.selection_label(actor, selected) if selected is not None else None
        )
        return web_response(
            render_templates_page(
                actor,
                selected,
                label,
                maximum_upload_bytes=settings.template_max_archive_bytes,
            )
        )

    @router.post("/login", include_in_schema=False)
    def browser_login(
        request: Request,
        username: Annotated[str, Form()],
        password: Annotated[str, Form()],
        _origin: Annotated[None, Depends(dependencies.enforce_login_origin)],
    ) -> Response:
        try:
            result = auth.login(
                username,
                password,
                previous_session_token=dependencies.session_token(request),
            )
        except AuthenticationError:
            return web_response(render_login_page(invalid=True), status_code=401)
        destination = (
            "/change-password" if result.user.password_change_required else "/convert"
        )
        response = RedirectResponse(destination, status_code=status.HTTP_303_SEE_OTHER)
        set_session_cookie(response, settings, result.session_token)
        set_csrf_cookie(response, settings, result.csrf_token)
        return response

    return router
