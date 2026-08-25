"""Executable package-native API and worker runtime assembly."""

from __future__ import annotations

import os
import signal
import sys
from collections.abc import Callable, Sequence
from threading import Event
from types import FrameType
from typing import Any

import uvicorn
from fastapi import FastAPI

from markweave.app import build_components, create_app
from markweave.config import ConfigurationError, Settings, StorageProfile
from markweave.conversion.processor import build_production_processor


def build_embedded_app(settings: Settings | None = None) -> FastAPI:
    """Build the one-process standalone API and embedded worker lifecycle."""

    resolved = settings or Settings.load()
    if resolved.storage_profile is not StorageProfile.STANDALONE:
        raise ConfigurationError("Embedded worker requires standalone storage")
    components = build_components(resolved)
    try:
        processor = build_production_processor(resolved, components.object_store)
        worker = components.build_embedded_worker(
            worker_id=f"embedded-{os.getpid()}",
            processor=processor,
            thread_name="md-converter-embedded-worker",
        )
        return create_app(
            resolved,
            components=components,
            embedded_worker=worker,
            embedded_worker_stop_timeout_seconds=(
                resolved.job_max_duration_seconds
                + resolved.template_engine_termination_grace_seconds
                + resolved.worker_heartbeat_seconds
            ),
            manage_components=True,
        )
    except Exception:
        components.close()
        raise


def run_external_worker(settings: Settings | None = None) -> None:
    """Run one distributed worker until SIGINT or SIGTERM requests shutdown."""

    resolved = settings or Settings.load()
    if resolved.storage_profile is not StorageProfile.DISTRIBUTED:
        raise ConfigurationError("External worker requires distributed storage")
    components = build_components(resolved)
    stop = Event()
    previous_handlers: dict[
        signal.Signals, int | Callable[[int, FrameType | None], Any] | None
    ] = {}

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    try:
        previous_handlers = {
            current: signal.signal(current, request_stop)
            for current in (signal.SIGINT, signal.SIGTERM)
        }
        processor = build_production_processor(resolved, components.object_store)
        runtime = components.build_external_worker_runtime(
            worker_id=f"external-{os.getpid()}", processor=processor
        )
        runtime.run(stop)
    finally:
        for current, previous in previous_handlers.items():
            signal.signal(current, previous)
        components.close()


def main(arguments: Sequence[str] | None = None) -> int:
    """Dispatch exactly one package runtime mode for the container entrypoint."""

    selected = tuple(sys.argv[1:] if arguments is None else arguments)
    if selected == ("embedded-worker",):
        uvicorn.run(
            build_embedded_app(),
            host=os.environ.get(
                "MD_CONVERTER_HOST",
                "0.0.0.0",  # noqa: S104 - container bind
            ),
            port=int(os.environ.get("MD_CONVERTER_PORT", "8080")),
            proxy_headers=False,
            server_header=False,
        )
        return 0
    if selected == ("external-worker",):
        run_external_worker()
        return 0
    raise SystemExit(
        "usage: python -m markweave.runtime <embedded-worker|external-worker>"
    )


if __name__ == "__main__":  # pragma: no cover - exercised by container entrypoints
    raise SystemExit(main())
