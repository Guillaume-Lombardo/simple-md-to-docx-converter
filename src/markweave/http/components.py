"""Profile-aware application component composition and ownership."""

import logging
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import import_module
from threading import Event, Lock
from time import monotonic
from typing import Any

from sqlalchemy import Engine

from markweave.auth.ports import ReadinessProbe
from markweave.auth.security import (
    Argon2idPasswordHasher,
    SecretsTokenGenerator,
    SystemClock,
)
from markweave.auth.service import AuthenticationService, SecurityRuntime, SessionPolicy
from markweave.config import (
    ConfigurationError,
    MalwareScanningMode,
    Settings,
    StorageProfile,
)
from markweave.jobs.ports import JobRepository
from markweave.jobs.runner import EmbeddedWorker, ExternalWorkerRuntime, WorkerLoop
from markweave.jobs.runtime import JobPolicies, build_job_policies
from markweave.jobs.service import JobService
from markweave.jobs.worker import ConversionWorker
from markweave.malware import (
    ClamAVUploadScanner,
    TrustedUpstreamUploadScanner,
    TrustingUploadScanner,
    UploadScanner,
)
from markweave.observability import (
    AuditReader,
    MetricsHttpServer,
    OperationalMetrics,
    QueueObserver,
    log_event,
)
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.observability import SqlAuditReader, SqlOperationalObserver
from markweave.persistence.retention import SqlRetentionRepository
from markweave.persistence.sql import (
    DatabaseReadinessProbe,
    SqlIdleSessionPolicyRepository,
    SqlSessionRepository,
    SqlUserRepository,
    create_database_engine,
    standalone_database_url,
)
from markweave.persistence.templates import (
    SqlTemplateCatalogRepository,
    SqlTemplateSelectionRepository,
)
from markweave.retention import DataRetentionPolicy, RetentionService
from markweave.storage import FilesystemObjectStore, ObjectStore, S3ObjectStore
from markweave.templates.processor import (
    TemplateAwareProcessor,
    build_template_conversion_worker,
)
from markweave.templates.runtime import build_template_validator
from markweave.templates.service import TemplateRecoveryPolicy, TemplateService


@dataclass(frozen=True, slots=True)
class AppComponents:
    """Application ports assembled independently of FastAPI."""

    authentication: AuthenticationService
    readiness: ReadinessProbe
    object_store: ObjectStore
    jobs: JobService
    scanner: UploadScanner = field(default_factory=TrustingUploadScanner)
    templates: TemplateService | None = None
    job_policies: JobPolicies | None = None
    retention: RetentionService | None = None
    job_repository: JobRepository | None = None
    metrics: OperationalMetrics = field(default_factory=OperationalMetrics)
    queue_observer: QueueObserver | None = None
    audit_reader: AuditReader | None = None
    worker_metrics_bind_host: str = "127.0.0.1"
    worker_metrics_port: int = 9464
    worker_metrics_max_connections: int = 4
    worker_metrics_observation_limit: int = 2
    worker_metrics_accept_queue_size: int = 8
    worker_metrics_request_timeout_seconds: float = 2.0
    owned_engines: tuple[Engine, ...] = field(default=(), repr=False, compare=False)
    owned_resources: tuple[Any, ...] = field(default=(), repr=False, compare=False)
    _close_lock: Lock = field(
        default_factory=Lock, init=False, repr=False, compare=False
    )
    _closed: Event = field(default_factory=Event, init=False, repr=False, compare=False)

    def close(self) -> None:
        """Cancel observations and close every application-owned resource."""

        if (
            not self.owned_engines
            and not self.owned_resources
            and self.queue_observer is None
        ):
            return
        with self._close_lock:
            if self._closed.is_set():
                return
            self._closed.set()
        with ExitStack() as cleanup:
            for resource in self.owned_resources:
                cleanup.callback(resource.close)
            for engine in self.owned_engines:
                cleanup.callback(engine.dispose)
            if self.queue_observer is not None:
                self.queue_observer.cancel_observations(
                    timeout_seconds=self.worker_metrics_request_timeout_seconds
                )

    def build_conversion_worker(
        self,
        *,
        worker_id: str,
        processor: TemplateAwareProcessor,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> ConversionWorker:
        """Assemble the production worker with all persistent maintenance."""

        if (
            self.templates is None
            or self.job_policies is None
            or self.retention is None
            or self.job_repository is None
        ):
            raise RuntimeError("Production worker components are incomplete")
        return build_template_conversion_worker(
            worker_id=worker_id,
            repository=self.job_repository,
            objects=self.object_store,
            resolver=self.templates,
            processor=processor,
            clock=clock,
            policy=self.job_policies.worker,
            maintenance=self.retention,
            monotonic_clock=monotonic_clock,
            metrics=self.metrics,
        )

    def build_external_worker_loop(
        self,
        *,
        worker_id: str,
        processor: TemplateAwareProcessor,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> WorkerLoop:
        """Assemble the shared production loop for an external worker process."""

        if self.job_policies is None:
            raise RuntimeError("Production worker policies are unavailable")
        worker = self.build_conversion_worker(
            worker_id=worker_id,
            processor=processor,
            clock=clock,
            monotonic_clock=monotonic_clock,
        )
        return WorkerLoop(
            worker,
            self.job_policies.schedule,
            monotonic_clock=monotonic_clock,
            metrics=self.metrics,
        )

    def build_embedded_worker(
        self,
        *,
        worker_id: str,
        processor: TemplateAwareProcessor,
        thread_name: str,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> EmbeddedWorker:
        """Assemble the standalone lifecycle from the same production loop."""

        return EmbeddedWorker(
            self.build_external_worker_loop(
                worker_id=worker_id,
                processor=processor,
                clock=clock,
                monotonic_clock=monotonic_clock,
            ),
            thread_name=thread_name,
        )

    def build_external_worker_runtime(
        self,
        *,
        worker_id: str,
        processor: TemplateAwareProcessor,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> ExternalWorkerRuntime:
        """Assemble the external loop with a process-local scrape listener."""

        if self.queue_observer is None:
            raise RuntimeError("External worker queue observation is unavailable")
        return ExternalWorkerRuntime(
            self.build_external_worker_loop(
                worker_id=worker_id,
                processor=processor,
                clock=clock,
                monotonic_clock=monotonic_clock,
            ),
            MetricsHttpServer(
                self.metrics,
                self.queue_observer,
                host=self.worker_metrics_bind_host,
                port=self.worker_metrics_port,
                max_connections=self.worker_metrics_max_connections,
                observation_limit=self.worker_metrics_observation_limit,
                accept_queue_size=self.worker_metrics_accept_queue_size,
                request_timeout_seconds=self.worker_metrics_request_timeout_seconds,
            ),
        )


class ProfileReadinessProbe:
    """Cheap readiness composition for metadata and object persistence."""

    def __init__(self, database: ReadinessProbe, objects: ReadinessProbe) -> None:
        self._database = database
        self._objects = objects

    def is_ready(self) -> bool:
        return self._database.is_ready() and self._objects.is_ready()


def build_upload_scanner(settings: Settings) -> UploadScanner:
    """Assemble the explicit upload-scanning trust boundary."""

    if settings.insecure_evaluation_mode:
        log_event("insecure_evaluation_mode_enabled", level=logging.WARNING)
        return TrustedUpstreamUploadScanner()
    if settings.malware_scanning_mode is MalwareScanningMode.TRUSTED_UPSTREAM:
        log_event(
            "malware_scanning_delegated_to_trusted_upstream",
            level=logging.WARNING,
        )
        return TrustedUpstreamUploadScanner()
    return ClamAVUploadScanner(
        settings.clamav_host,
        settings.clamav_port,
        settings.clamav_timeout_seconds,
    )


def build_components(  # noqa: PLR0915 - explicit resource ownership composition
    settings: Settings,
) -> AppComponents:
    """Assemble the selected coherent persistent storage profile."""

    job_policies = build_job_policies(settings)
    owned_resources: tuple[Any, ...] = ()
    if settings.storage_profile is StorageProfile.STANDALONE:
        data_directory = settings.standalone_data_directory
        if data_directory is None:
            raise RuntimeError("Validated standalone settings are incomplete")
        database_url = standalone_database_url(data_directory)
        object_store: ObjectStore = FilesystemObjectStore(data_directory)
        object_readiness: ReadinessProbe = FilesystemObjectStore(data_directory)
    else:
        boto3, config_class = _load_distributed_dependencies()
        database_secret = settings.distributed_database_url
        bucket = settings.s3_bucket
        if database_secret is None or bucket is None:
            raise RuntimeError("Validated distributed settings are incomplete")
        database_url = database_secret.get_secret_value()
        client_options: dict[str, Any] = {}
        if settings.s3_endpoint_url is not None:
            client_options["endpoint_url"] = settings.s3_endpoint_url
        if settings.s3_region is not None:
            client_options["region_name"] = settings.s3_region
        if settings.s3_access_key_id is not None:
            client_options["aws_access_key_id"] = (
                settings.s3_access_key_id.get_secret_value()
            )
            client_options["aws_secret_access_key"] = (
                settings.s3_secret_access_key.get_secret_value()
                if settings.s3_secret_access_key is not None
                else ""
            )
        object_store = S3ObjectStore(boto3.client("s3", **client_options), bucket)
        try:
            readiness_client_options = {
                **client_options,
                "config": config_class(
                    connect_timeout=settings.readiness_timeout_seconds,
                    read_timeout=settings.readiness_timeout_seconds,
                    retries={"max_attempts": 0},
                ),
            }
            object_readiness = S3ObjectStore(
                boto3.client("s3", **readiness_client_options), bucket
            )
        except BaseException:
            object_store.close()
            raise
        owned_resources = (object_store, object_readiness)

    with ExitStack() as pending_engines:
        for resource in owned_resources:
            pending_engines.callback(resource.close)
        engine = create_database_engine(database_url)
        pending_engines.callback(engine.dispose)
        upgrade_database(engine)
        readiness_engine = create_database_engine(
            database_url,
            timeout_seconds=settings.readiness_timeout_seconds,
            pool_pre_ping=False,
        )
        pending_engines.callback(readiness_engine.dispose)
        observation_engine = create_database_engine(
            database_url,
            timeout_seconds=settings.worker_metrics_request_timeout_seconds,
            pool_pre_ping=False,
        )
        pending_engines.callback(observation_engine.dispose)
        users = SqlUserRepository(engine)
        sessions = SqlSessionRepository(engine)
        hasher = Argon2idPasswordHasher(
            memory_cost=settings.argon2_memory_cost,
            time_cost=settings.argon2_time_cost,
            parallelism=settings.argon2_parallelism,
        )
        authentication = AuthenticationService(
            users=users,
            sessions=sessions,
            security=SecurityRuntime(
                hasher=hasher,
                tokens=SecretsTokenGenerator(settings.session_token_bytes),
                clock=SystemClock(),
            ),
            policy=SessionPolicy(
                absolute_seconds=settings.session_absolute_seconds,
            ),
            idle_policies=SqlIdleSessionPolicyRepository(engine),
        )
        job_repository = SqlJobRepository(engine, job_policies.admission)
        jobs = JobService(job_repository, object_store, job_policies.service)
        templates = TemplateService(
            catalog=SqlTemplateCatalogRepository(engine),
            selections=SqlTemplateSelectionRepository(engine),
            objects=object_store,
            validate_content=build_template_validator(settings),
            recovery_policy=TemplateRecoveryPolicy(
                settings.template_pending_publication_stale_seconds
            ),
        )
        templates.reclaim_pending()
        retention = RetentionService(
            SqlRetentionRepository(engine),
            object_store,
            DataRetentionPolicy(
                template_version_seconds=settings.template_version_retention_seconds,
                audit_seconds=settings.audit_retention_seconds,
                minimum_template_versions=settings.template_min_retained_versions,
                claim_lease_seconds=settings.worker_lease_seconds,
            ),
        )
        metrics = OperationalMetrics()
        components = AppComponents(
            authentication=authentication,
            readiness=ProfileReadinessProbe(
                DatabaseReadinessProbe(readiness_engine), object_readiness
            ),
            object_store=object_store,
            jobs=jobs,
            scanner=build_upload_scanner(settings),
            templates=templates,
            job_policies=job_policies,
            retention=retention,
            job_repository=job_repository,
            metrics=metrics,
            queue_observer=SqlOperationalObserver(
                observation_engine,
                default_timeout_seconds=settings.worker_metrics_request_timeout_seconds,
            ),
            audit_reader=SqlAuditReader(engine),
            worker_metrics_bind_host=settings.worker_metrics_bind_host,
            worker_metrics_port=settings.worker_metrics_port,
            worker_metrics_max_connections=settings.worker_metrics_max_connections,
            worker_metrics_observation_limit=settings.worker_metrics_observation_limit,
            worker_metrics_accept_queue_size=settings.worker_metrics_accept_queue_size,
            worker_metrics_request_timeout_seconds=(
                settings.worker_metrics_request_timeout_seconds
            ),
            owned_engines=(engine, readiness_engine, observation_engine),
            owned_resources=owned_resources,
        )
        pending_engines.pop_all()
        return components


def _load_distributed_dependencies() -> tuple[Any, type[Any]]:
    """Load distributed-only clients after the selected profile is known."""

    try:
        import_module("psycopg")
    except ModuleNotFoundError:
        raise ConfigurationError(
            "PostgreSQL storage requires the 'distributed' extra; "
            "install 'markweave[distributed]'."
        ) from None
    try:
        boto3 = import_module("boto3")
        config_class = import_module("botocore.config").Config
    except ModuleNotFoundError:
        raise ConfigurationError(
            "S3 object storage requires the 'distributed' extra; "
            "install 'markweave[distributed]'."
        ) from None
    return boto3, config_class
