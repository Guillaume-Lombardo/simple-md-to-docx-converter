"""Application bootstrap and lifespan ownership."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

from fastapi import FastAPI

from markweave.config import Settings
from markweave.jobs.runner import EmbeddedWorker
from markweave.malware import UploadScanner

from .components import AppComponents, build_components


def resolve_components(
    settings: Settings,
    *,
    components: AppComponents | None,
    scanner: UploadScanner | None,
    manage_components: bool,
    component_builder=build_components,
) -> tuple[AppComponents, bool]:
    """Resolve ports, bootstrap identities, and close owned resources on failure."""

    resolved = components or component_builder(settings)
    owns_components = components is None or manage_components
    if scanner is not None:
        resolved = replace(resolved, scanner=scanner)
    try:
        resolved.authentication.bootstrap_admin(
            settings.initial_admin_username,
            settings.initial_admin_password.get_secret_value(),
        )
        if settings.user_provisioning_file is not None:
            resolved.authentication.provision_users(settings.user_provisioning_file)
    except Exception:
        if owns_components:
            resolved.close()
        raise
    return resolved, owns_components


def build_lifespan(
    components: AppComponents,
    *,
    owns_components: bool,
    embedded_worker: EmbeddedWorker | None,
    embedded_worker_stop_timeout_seconds: float,
):
    """Build the exact worker-before-components application lifespan."""

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        del _app
        worker_started = False
        try:
            if embedded_worker is not None:
                embedded_worker.start()
                worker_started = True
            yield
        finally:
            try:
                if embedded_worker is not None and worker_started:
                    embedded_worker.stop(
                        timeout_seconds=embedded_worker_stop_timeout_seconds
                    )
            finally:
                if owns_components:
                    components.close()

    return lifespan
