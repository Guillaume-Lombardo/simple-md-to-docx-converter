"""Profile-aware application component composition and ownership."""

import logging
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from importlib import import_module
from threading import Event, Lock
from time import monotonic
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine

from markweave.auth.ports import ReadinessProbe
from markweave.auth.security import (
    Argon2idPasswordHasher,
    SecretsTokenGenerator,
    SystemClock,
)
from markweave.auth.service import AuthenticationService, SecurityRuntime, SessionPolicy
from markweave.composer.admin_policy import ComposerAdminPolicy
from markweave.composer.author_knowledge import AuthorKnowledgeLimits
from markweave.composer.connections import ConnectionService
from markweave.composer.runtime import build_connection_policy, build_connection_service
from markweave.config import (
    ConfigurationError,
    MalwareScanningMode,
    Settings,
    StorageProfile,
)
from markweave.conversion.runtime_manifest import COMPONENT_VERSIONS
from markweave.http.composer_step_runner import ComposerStepRunner
from markweave.jobs.ports import JobRepository
from markweave.jobs.runner import (
    EmbeddedWorker,
    ExternalWorkerRuntime,
    RunnableWorkerLoop,
    WorkerLoop,
)
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
from markweave.persistence.composer import (
    SqlComposerAuditRepository,
    SqlComposerModelStepRepository,
    SqlComposerRepository,
    SqlConnectionRepository,
)
from markweave.persistence.composer.admin_policy import SqlComposerAdminPolicyRepository
from markweave.persistence.composer.author_knowledge import SqlAuthorKnowledgeRepository
from markweave.persistence.composer.fill_plans import SqlFillPlanRepository
from markweave.persistence.composer.typed_templates import SqlTypedTemplateRepository
from markweave.persistence.jobs import SqlJobRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.observability import SqlAuditReader, SqlOperationalObserver
from markweave.persistence.retention import SqlRetentionRepository
from markweave.persistence.reversion_jobs import SqlReversionJobRepository
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
from markweave.reversion_jobs.policy import ReversionAdmissionPolicy
from markweave.reversion_jobs.reconciliation import ReversionBrokerReconciler
from markweave.reversion_jobs.runner import (
    FairWorkerLoop,
    ReversionSchedule,
    StopSignalBridge,
)
from markweave.reversion_jobs.runtime import (
    ReversionBrokerClient,
    ReversionExecutionPolicies,
    ReversionWorkerRuntime,
    build_reversion_broker_client,
    build_reversion_execution_policies,
)
from markweave.reversion_jobs.service import (
    ReversionService,
    ReversionServicePolicy,
)
from markweave.reversion_jobs.worker import ReversionWorker
from markweave.storage import BoundedObjectStore, FilesystemObjectStore, S3ObjectStore
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
    object_store: BoundedObjectStore
    jobs: JobService
    scanner: UploadScanner = field(default_factory=TrustingUploadScanner)
    reversions: ReversionService | None = None
    templates: TemplateService | None = None
    job_policies: JobPolicies | None = None
    retention: RetentionService | None = None
    job_repository: JobRepository | None = None
    reversion_repository: SqlReversionJobRepository | None = None
    reversion_policies: ReversionExecutionPolicies | None = None
    reversion_broker: ReversionBrokerClient | None = None
    composer_store: SqlComposerRepository | None = None
    composer_connection_repository: SqlConnectionRepository | None = None
    composer_admin_policy: ComposerAdminPolicy | None = None
    composer_connections: ConnectionService | None = None
    composer_model_step_repository: SqlComposerModelStepRepository | None = None
    composer_steps: ComposerStepRunner | None = None
    composer_authors: SqlAuthorKnowledgeRepository | None = None
    composer_fill_templates: SqlTypedTemplateRepository | None = None
    composer_fill_plans: SqlFillPlanRepository | None = None
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
            runtime_component_versions=COMPONENT_VERSIONS,
        )

    def build_external_worker_loop(
        self,
        *,
        worker_id: str,
        processor: TemplateAwareProcessor,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = monotonic,
    ) -> RunnableWorkerLoop:
        """Assemble the shared production loop for an external worker process."""

        if self.job_policies is None:
            raise RuntimeError("Production worker policies are unavailable")
        worker = self.build_conversion_worker(
            worker_id=worker_id,
            processor=processor,
            clock=clock,
            monotonic_clock=monotonic_clock,
        )
        reverse = self._build_reversion_worker(
            worker_id=worker_id,
            clock=clock,
            monotonic_clock=monotonic_clock,
        )
        if reverse is None:
            return WorkerLoop(
                worker,
                self.job_policies.schedule,
                monotonic_clock=monotonic_clock,
                metrics=self.metrics,
            )
        reverse_worker, stop_bridge = reverse
        policies = self.reversion_policies
        if policies is None:  # pragma: no cover - narrowed by helper result
            raise RuntimeError("Reverse production worker policies are incomplete")
        return FairWorkerLoop(
            worker,
            reverse_worker,
            self.job_policies.schedule,
            ReversionSchedule(
                policies.cleanup_interval_seconds,
                policies.error_backoff_seconds,
            ),
            stop_bridge=stop_bridge,
            monotonic_clock=monotonic_clock,
            metrics=self.metrics,
        )

    def _build_reversion_worker(
        self,
        *,
        worker_id: str,
        clock: Callable[[], datetime],
        monotonic_clock: Callable[[], float],
    ) -> tuple[ReversionWorker, StopSignalBridge] | None:
        policies = self.reversion_policies
        if policies is None:
            return None
        if self.reversion_repository is None or self.reversion_broker is None:
            raise RuntimeError("Reverse production worker components are incomplete")
        stop_bridge = StopSignalBridge()
        reconciler = ReversionBrokerReconciler(
            self.reversion_repository,
            self.reversion_broker,
            ack_batch_limit=policies.reconciliation_ack_batch_size,
            request_id_factory=uuid4,
        )
        runtime = ReversionWorkerRuntime(
            repository=self.reversion_repository,
            objects=self.object_store,
            broker=self.reversion_broker,
            reconciler=reconciler,
            principal=policies.principal,
            broker_policy=policies.broker_policy,
            content_limits=policies.content_limits,
            policy=policies.worker,
            worker_id=f"{worker_id}-reverse",
            clock=clock,
            monotonic_clock=monotonic_clock,
            wait=stop_bridge.wait,
            shutdown_requested=stop_bridge.is_set,
            request_id_factory=uuid4,
            require_ready=True,
            metrics=self.metrics,
        )
        return ReversionWorker(runtime), stop_bridge

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


def _build_composer_step_runtime(  # noqa: PLR0913, PLR0917 - explicit runtime ports
    settings: Settings,
    engine: Engine,
    connections: ConnectionService | None,
    metrics: OperationalMetrics,
    recovery_limit: int,
    authors: SqlAuthorKnowledgeRepository | None = None,
) -> tuple[SqlComposerModelStepRepository, ComposerStepRunner | None, tuple[Any, ...]]:
    repository = SqlComposerModelStepRepository(
        engine,
        on_expiration=metrics.record_composer_model_step_expiration,
        on_recovery=metrics.record_composer_model_step_recovery,
        authors=authors,
    )
    repository.recover_stale_model_steps(
        stale_before=datetime.now(UTC), limit=recovery_limit
    )
    if (
        connections is None
        or settings.composer_maximum_concurrent_calls is None
        or settings.composer_timeout_seconds is None
        or settings.composer_pending_publication_stale_seconds is None
    ):
        return repository, None, ()
    runner = ComposerStepRunner(
        repository,
        connections,
        maximum_active=settings.composer_maximum_concurrent_calls,
        metrics=metrics,
        lease=timedelta(
            seconds=(
                settings.composer_timeout_seconds
                + settings.composer_pending_publication_stale_seconds
            )
        ),
    )
    return repository, runner, (runner,)


def build_components(  # noqa: PLR0912, PLR0915 - explicit resource ownership composition
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
        object_store: BoundedObjectStore = FilesystemObjectStore(data_directory)
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
        reversions: ReversionService | None = None
        reversion_repository: SqlReversionJobRepository | None = None
        reversion_retention = settings.reversion_result_retention_seconds
        reversion_owner_limit = settings.reversion_active_limit_per_user
        if (
            settings.reversion_upload_max_bytes is not None
            and settings.reversion_request_max_bytes is not None
            and settings.reversion_retry_after_seconds is not None
            and reversion_retention is not None
            and reversion_owner_limit is not None
        ):
            reversion_repository = SqlReversionJobRepository(
                engine,
                ReversionAdmissionPolicy(
                    active_jobs_per_user=reversion_owner_limit,
                    global_queue_capacity=settings.job_global_queue_capacity,
                ),
            )
            reversions = ReversionService(
                reversion_repository,
                object_store,
                ReversionServicePolicy(
                    reversion_retention, settings.reversion_upload_max_bytes
                ),
            )
        reversion_policies = (
            build_reversion_execution_policies(settings)
            if settings.reversion_execution_configured
            else None
        )
        if reversion_policies is not None and reversion_repository is None:
            reversion_repository = SqlReversionJobRepository(engine)
        reversion_broker = (
            build_reversion_broker_client(settings, reversion_policies)
            if reversion_policies is not None
            else None
        )
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
        composer_store = SqlComposerRepository(engine, object_store)
        composer_authors = (
            SqlAuthorKnowledgeRepository(
                engine,
                AuthorKnowledgeLimits(
                    max_fields=settings.template_max_xml_elements,
                    max_name_length=settings.template_max_name_characters,
                    max_field_value_length=settings.composer_maximum_request_bytes,
                    max_field_name_length=settings.template_max_name_characters,
                    max_citation_length=settings.template_metadata_request_max_bytes,
                ),
            )
            if settings.composer_maximum_request_bytes is not None
            else None
        )
        composer_fill_templates = (
            SqlTypedTemplateRepository(
                engine,
                object_store,
                publication_lease=timedelta(
                    seconds=settings.template_pending_publication_stale_seconds
                ),
            )
            if composer_authors is not None
            else None
        )
        composer_fill_plans = (
            SqlFillPlanRepository(engine)
            if composer_fill_templates is not None
            else None
        )
        if composer_fill_templates is not None:
            composer_fill_templates.recover_pending(
                limit=job_policies.schedule.cleanup_limit
            )
        if settings.composer_pending_publication_stale_seconds is not None:
            stale_before = datetime.now(UTC) - timedelta(
                seconds=settings.composer_pending_publication_stale_seconds
            )
            composer_store.recover_stale_publications(stale_before=stale_before)
            composer_store.recover_stale_sources(stale_before=stale_before)
        composer_connection_repository = SqlConnectionRepository(
            engine,
            maximum_allowed_users=settings.composer_maximum_allowed_users,
        )
        composer_admin_policy = None
        if settings.composer_enabled:
            composer_admin_policy = ComposerAdminPolicy(
                SqlComposerAdminPolicyRepository(engine),
                build_connection_policy(settings),
                delegated=settings.composer_admin_policy_delegated,
            )
        try:
            composer_connections = build_connection_service(
                settings, composer_connection_repository, composer_admin_policy
            )
        except ConfigurationError:
            # An unavailable or mismatched key closes model and credential paths,
            # while conversion and authorized draft reads remain usable.
            log_event("composer_connection_unavailable", level=logging.WARNING)
            composer_admin_policy = None
            composer_connections = None
        metrics = OperationalMetrics()
        composer_model_step_repository, composer_steps, step_resources = (
            _build_composer_step_runtime(
                settings,
                engine,
                composer_connections,
                metrics,
                job_policies.schedule.cleanup_limit,
                composer_authors,
            )
        )
        owned_resources = (*owned_resources, *step_resources)
        retention = RetentionService(
            SqlRetentionRepository(engine),
            object_store,
            DataRetentionPolicy(
                template_version_seconds=settings.template_version_retention_seconds,
                audit_seconds=settings.audit_retention_seconds,
                minimum_template_versions=settings.template_min_retained_versions,
                claim_lease_seconds=settings.worker_lease_seconds,
                composer_draft_seconds=settings.composer_draft_retention_seconds,
            ),
            composer=composer_store,
            composer_content_audit=SqlComposerAuditRepository(engine),
            composer_connection_audit=composer_connection_repository,
            composer_model_steps=composer_model_step_repository,
        )
        components = AppComponents(
            authentication=authentication,
            readiness=ProfileReadinessProbe(
                DatabaseReadinessProbe(readiness_engine), object_readiness
            ),
            object_store=object_store,
            jobs=jobs,
            scanner=build_upload_scanner(settings),
            reversions=reversions,
            templates=templates,
            job_policies=job_policies,
            retention=retention,
            job_repository=job_repository,
            reversion_repository=reversion_repository,
            reversion_policies=reversion_policies,
            reversion_broker=reversion_broker,
            composer_store=composer_store,
            composer_connection_repository=composer_connection_repository,
            composer_admin_policy=composer_admin_policy,
            composer_connections=composer_connections,
            composer_model_step_repository=composer_model_step_repository,
            composer_steps=composer_steps,
            composer_authors=composer_authors,
            composer_fill_templates=composer_fill_templates,
            composer_fill_plans=composer_fill_plans,
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
