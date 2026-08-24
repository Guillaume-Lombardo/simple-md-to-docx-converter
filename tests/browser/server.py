"""Deterministic application used by the real-browser acceptance test."""

from __future__ import annotations

import argparse
import hashlib
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, cast
from uuid import UUID, uuid4

import uvicorn
from fastapi import FastAPI, Request

from md_converter.app import AppComponents, create_app
from md_converter.auth.memory import (
    MemoryReadinessProbe,
    MemorySessionRepository,
    MemoryUserRepository,
)
from md_converter.auth.models import User
from md_converter.auth.security import (
    Argon2idPasswordHasher,
    SecretsTokenGenerator,
    SystemClock,
)
from md_converter.auth.service import (
    AuthenticationService,
    SecurityRuntime,
    SessionPolicy,
)
from md_converter.config import Settings
from md_converter.jobs.models import (
    ConversionJob,
    JobOutput,
    JobPage,
    JobRequest,
    JobState,
    JobStep,
)
from md_converter.jobs.service import JobService
from md_converter.storage import ObjectKey, ObjectNotFoundError
from md_converter.templates.errors import TemplateStorageError
from md_converter.templates.models import (
    TemplateIdentity,
    TemplatePage,
    TemplateSearch,
    TemplateStatus,
)
from md_converter.templates.service import TemplateService
from tests.settings import template_settings

USERNAME = "browser-admin"
PASSWORD = "browser-password"  # noqa: S105 - fixed isolated test credential


class MemoryObjectStore:
    """Small complete object-store port for application composition."""

    def __init__(self) -> None:
        self._objects: dict[ObjectKey, bytes] = {}

    def put(self, key: ObjectKey, content: bytes) -> None:
        self._objects[key] = content

    def get(self, key: ObjectKey) -> bytes:
        try:
            return self._objects[key]
        except KeyError:
            raise ObjectNotFoundError("Object does not exist") from None

    def delete(self, key: ObjectKey) -> None:
        self._objects.pop(key, None)

    def exists(self, key: ObjectKey) -> bool:
        return key in self._objects

    def is_ready(self) -> bool:
        return True


class BrowserTemplateService:
    """Expose a preferred default plus a searchable alternate template."""

    def __init__(self) -> None:
        owner_id = uuid4()
        self.preferred = TemplateIdentity(
            uuid4(),
            owner_id,
            "Preferred report",
            "The default template",
            TemplateStatus.ACTIVE,
            current_version_id=uuid4(),
        )
        self.alternate = TemplateIdentity(
            uuid4(),
            owner_id,
            "Alternate brief",
            "A searchable template",
            TemplateStatus.ACTIVE,
            current_version_id=uuid4(),
        )

    def resolve(self, _actor: User) -> TemplateIdentity:
        return self.preferred

    def selection_label(self, _actor: User, _template: TemplateIdentity) -> str:
        return "Preferred template"

    def search(self, _actor: User, query: TemplateSearch) -> TemplatePage:
        normalized = (query.name or "").casefold()
        if normalized == "error":
            raise TemplateStorageError("test-only storage failure")
        items = tuple(
            template
            for template in (self.preferred, self.alternate)
            if not normalized or normalized in template.name.casefold()
        )
        return TemplatePage(items, len(items), query.offset, query.limit)


class BrowserJobService:
    """Drive queued, successful, cancelling, and expired UI states deterministically."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._jobs: dict[UUID, ConversionJob] = {}
        self._keys: dict[tuple[UUID, str], UUID] = {}
        self._polls: dict[UUID, int] = {}
        self.idempotency_keys: list[str | None] = []
        self.outputs: list[str] = []
        self.cancelled_ids: list[str] = []
        self.poll_times: dict[UUID, list[float]] = {}

    def submit(
        self, request: JobRequest, idempotency_key: str | None
    ) -> tuple[ConversionJob, bool]:
        with self._lock:
            self.idempotency_keys.append(idempotency_key)
            key = (request.owner_id, idempotency_key or "")
            existing_id = self._keys.get(key) if idempotency_key else None
            if existing_id is not None:
                return self._jobs[existing_id], True
            now = request.now
            job = ConversionJob(
                id=uuid4(),
                owner_id=request.owner_id,
                source_object_id=uuid4(),
                template_id=request.template_id,
                template_version_id=request.template_version_id,
                output=request.output,
                component_versions=request.component_versions,
                state=JobState.QUEUED,
                step=JobStep.QUEUED,
                progress=0,
                request_digest=hashlib.sha256(request.source).hexdigest(),
                idempotency_digest=(
                    hashlib.sha256(idempotency_key.encode()).hexdigest()
                    if idempotency_key
                    else None
                ),
                created_at=now,
                updated_at=now,
            )
            self._jobs[job.id] = job
            self._polls[job.id] = 0
            self.poll_times[job.id] = []
            if idempotency_key:
                self._keys[key] = job.id
            self.outputs.append(request.output.value)
            return job, False

    def get_visible(
        self, job_id: UUID, *, actor_id: UUID, actor_is_admin: bool
    ) -> ConversionJob:
        del actor_is_admin
        with self._lock:
            job = self._jobs[job_id]
            if job.owner_id != actor_id:
                raise KeyError(job_id)
            self.poll_times[job_id].append(time.monotonic())
            count = self._polls[job_id] + 1
            self._polls[job_id] = count
            if job.output is JobOutput.DOCX:
                job = self._running(job) if count == 1 else self._succeeded(job)
            elif job.output is JobOutput.PDF and job.cancel_requested:
                job = (
                    self._running(job, cancel_requested=True)
                    if count == 1
                    else self._cancelled(job)
                )
            elif job.output is JobOutput.BOTH:
                job = self._expired(job)
            self._jobs[job_id] = job
            return job

    def list_owner(self, owner_id: UUID, *, offset: int, limit: int) -> JobPage:
        with self._lock:
            items = tuple(
                job
                for job in reversed(tuple(self._jobs.values()))
                if job.owner_id == owner_id
            )
            return JobPage(items[offset : offset + limit], len(items), offset, limit)

    def cancel(
        self,
        job_id: UUID,
        *,
        actor_id: UUID,
        actor_is_admin: bool,
        now: datetime,
    ) -> ConversionJob:
        del actor_is_admin
        with self._lock:
            job = self._jobs[job_id]
            if job.owner_id != actor_id:
                raise KeyError(job_id)
            self.cancelled_ids.append(str(job_id))
            job = replace(job, cancel_requested=True, updated_at=now)
            self._jobs[job_id] = job
            return job

    def download(
        self, job_id: UUID, *, actor_id: UUID, actor_is_admin: bool
    ) -> tuple[ConversionJob, bytes]:
        del actor_is_admin
        with self._lock:
            job = self._jobs[job_id]
            if job.owner_id != actor_id or job.state is not JobState.SUCCEEDED:
                raise KeyError(job_id)
            return job, b"browser acceptance result"

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "idempotency_keys": self.idempotency_keys,
                "outputs": self.outputs,
                "cancelled_ids": self.cancelled_ids,
                "poll_times": {
                    str(job_id): values for job_id, values in self.poll_times.items()
                },
            }

    @staticmethod
    def _running(
        job: ConversionJob, *, cancel_requested: bool = False
    ) -> ConversionJob:
        now = datetime.now(UTC)
        return replace(
            job,
            state=JobState.RUNNING,
            step=JobStep.RENDERING,
            progress=45,
            attempt=1,
            lease_owner="browser-worker",
            lease_token=uuid4(),
            lease_expires_at=now + timedelta(minutes=1),
            heartbeat_at=now,
            cancel_requested=cancel_requested,
            updated_at=now,
        )

    @staticmethod
    def _succeeded(job: ConversionJob) -> ConversionJob:
        now = datetime.now(UTC)
        return replace(
            job,
            state=JobState.SUCCEEDED,
            step=JobStep.COMPLETE,
            progress=100,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=None,
            result_object_id=uuid4(),
            expires_at=now + timedelta(minutes=1),
            updated_at=now,
        )

    @staticmethod
    def _cancelled(job: ConversionJob) -> ConversionJob:
        now = datetime.now(UTC)
        return replace(
            job,
            state=JobState.CANCELLED,
            step=JobStep.RENDERING,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=None,
            updated_at=now,
            expires_at=now + timedelta(minutes=1),
        )

    @staticmethod
    def _expired(job: ConversionJob) -> ConversionJob:
        now = datetime.now(UTC)
        return replace(
            job,
            state=JobState.EXPIRED,
            step=JobStep.COMPLETE,
            lease_owner=None,
            lease_token=None,
            lease_expires_at=None,
            heartbeat_at=None,
            updated_at=now,
            expires_at=now,
        )


def build_app(data_directory: Path) -> FastAPI:
    """Build the production web stack around deterministic boundary fakes."""
    authentication = AuthenticationService(
        users=MemoryUserRepository(),
        sessions=MemorySessionRepository(),
        security=SecurityRuntime(
            Argon2idPasswordHasher(memory_cost=8, time_cost=1, parallelism=1),
            SecretsTokenGenerator(32),
            SystemClock(),
        ),
        policy=SessionPolicy(idle_seconds=1_800, absolute_seconds=28_800),
    )
    jobs = BrowserJobService()
    app = create_app(
        Settings(
            **template_settings(),
            initial_admin_username=USERNAME,
            initial_admin_password=PASSWORD,
            argon2_memory_cost=8,
            argon2_time_cost=1,
            storage_profile="standalone",
            standalone_data_directory=data_directory,
            conversion_upload_max_bytes=128_000,
            conversion_request_max_bytes=256_000,
            conversion_retry_after_seconds=1,
            job_result_retention_seconds=60,
        ),
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=MemoryObjectStore(),
            jobs=cast("JobService", jobs),
            templates=cast("TemplateService", BrowserTemplateService()),
        ),
    )
    app.state.last_login_origin = None

    @app.middleware("http")
    async def capture_login_origin(request: Request, call_next: Any) -> Any:
        if request.url.path == "/login" and request.method == "POST":
            app.state.last_login_origin = {
                "origin": request.headers.get("Origin"),
                "base_url": str(request.base_url),
            }
        return await call_next(request)

    @app.get("/__test/state", include_in_schema=False)
    def test_state() -> dict[str, Any]:
        return {**jobs.snapshot(), "last_login_origin": app.state.last_login_origin}

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data", type=Path, required=True)
    arguments = parser.parse_args()
    uvicorn.run(
        build_app(arguments.data),
        host="localhost",
        port=arguments.port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
