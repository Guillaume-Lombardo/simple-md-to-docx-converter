"""FastAPI application composition root."""

from fastapi import FastAPI

from markweave.config import Settings
from markweave.http.components import AppComponents, build_components
from markweave.http.dependencies import HttpDependencies
from markweave.http.errors import install_error_handlers
from markweave.http.lifecycle import build_lifespan, resolve_components
from markweave.http.middleware import BoundedRequestBody
from markweave.http.openapi import document_openapi_contract
from markweave.http.routers import (
    administration,
    audit_observability,
    authentication,
    conversions,
    templates,
)
from markweave.jobs.runner import EmbeddedWorker
from markweave.malware import UploadScanner
from markweave.observability import CorrelationMiddleware
from markweave.version import VERSION


def create_app(  # noqa: PLR0913 - explicit lifecycle composition inputs
    settings: Settings | None = None,
    *,
    components: AppComponents | None = None,
    scanner: UploadScanner | None = None,
    embedded_worker: EmbeddedWorker | None = None,
    embedded_worker_stop_timeout_seconds: float = 30.0,
    manage_components: bool = False,
) -> FastAPI:
    """Create a configured application or fail before serving requests."""

    resolved_settings = settings if settings is not None else Settings.load()
    resolved_components, owns_components = resolve_components(
        resolved_settings,
        components=components,
        scanner=scanner,
        manage_components=manage_components,
        component_builder=build_components,
    )
    lifespan = build_lifespan(
        resolved_components,
        owns_components=owns_components,
        embedded_worker=embedded_worker,
        embedded_worker_stop_timeout_seconds=embedded_worker_stop_timeout_seconds,
    )
    app = FastAPI(
        title="Markdown Converter API",
        version=VERSION,
        docs_url="/docs",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        BoundedRequestBody,
        conversion_maximum_bytes=resolved_settings.conversion_request_max_bytes,
        template_maximum_bytes=resolved_settings.template_request_max_bytes,
        template_metadata_maximum_bytes=(
            resolved_settings.template_metadata_request_max_bytes
        ),
    )
    app.add_middleware(
        CorrelationMiddleware,
        metrics=resolved_components.metrics,
    )
    app.state.components = resolved_components
    app.state.conversion_retry_after_seconds = (
        resolved_settings.conversion_retry_after_seconds
    )
    install_error_handlers(app)

    public_origin = (
        str(resolved_settings.public_origin).rstrip("/").casefold()
        if resolved_settings.public_origin is not None
        else None
    )
    dependencies = HttpDependencies(
        settings=resolved_settings,
        components=resolved_components,
        authentication=resolved_components.authentication,
        public_origin=public_origin,
    )
    routers = (
        audit_observability.build_router(dependencies, embedded_worker),
        authentication.build_router(dependencies),
        administration.build_router(dependencies),
        conversions.build_router(dependencies),
        templates.build_router(dependencies),
    )
    for router in routers:
        app.router.routes.extend(router.routes)
    document_openapi_contract(
        app, session_cookie_name=resolved_settings.session_cookie_name
    )
    return app
