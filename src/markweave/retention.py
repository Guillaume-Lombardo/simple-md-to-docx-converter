"""Bounded retention contracts for immutable template versions and audit records."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from markweave.storage import ObjectKey, ObjectScope, ObjectStore
from markweave.templates.models import TemplateVersion

MINIMUM_PROTECTED_TEMPLATE_VERSIONS = 10


@dataclass(frozen=True, slots=True)
class DataRetentionPolicy:
    """Approved retention windows plus the invariant version floor."""

    template_version_seconds: int
    audit_seconds: int
    minimum_template_versions: int
    claim_lease_seconds: float
    composer_draft_seconds: int | None = None

    def __post_init__(self) -> None:
        if (
            self.template_version_seconds <= 0
            or self.audit_seconds <= 0
            or self.minimum_template_versions < MINIMUM_PROTECTED_TEMPLATE_VERSIONS
            or not 0 < self.claim_lease_seconds < float("inf")
            or (
                self.composer_draft_seconds is not None
                and self.composer_draft_seconds <= 0
            )
        ):
            raise ValueError("Data retention policy is invalid")


@dataclass(frozen=True, slots=True)
class RetentionClaim:
    """One fenced template-version deletion candidate."""

    version: TemplateVersion
    token: UUID


class RetentionRepository(Protocol):
    """Persistence boundary for bounded, traceable retention."""

    def claim_template_versions(
        self,
        *,
        cutoff_at: datetime,
        now: datetime,
        lease_expires_at: datetime,
        minimum_versions: int,
        limit: int,
    ) -> tuple[RetentionClaim, ...]: ...

    def complete_template_version(
        self, claim: RetentionClaim, *, completed_at: datetime
    ) -> bool: ...

    def cleanup_audits(
        self, *, cutoff_at: datetime, completed_at: datetime, limit: int
    ) -> int: ...


class ComposerRetentionRepository(Protocol):
    """Owner-scoped Composer cleanup after the configured retention window."""

    def cleanup_expired_drafts(self, *, cutoff_at: datetime, limit: int) -> int: ...

    def cleanup_orphan_sources(self, *, limit: int) -> int: ...


class ComposerContentAuditRepository(Protocol):
    """Bounded cleanup for content-free Composer mutation audit events."""

    def cleanup_content_audit(self, *, cutoff_at: datetime, limit: int) -> int: ...


class ComposerConnectionAuditRepository(Protocol):
    """Bounded cleanup for content-free Composer connection audit events."""

    def cleanup_connection_audit(self, *, cutoff_at: datetime, limit: int) -> int: ...


class ComposerModelStepMaintenanceRepository(Protocol):
    """Bounded recovery of expired durable Composer model attempts."""

    def recover_stale_model_steps(
        self, *, stale_before: datetime, limit: int
    ) -> int: ...


class RetentionService:
    """Delete eligible bytes before fenced metadata acknowledgement."""

    def __init__(  # noqa: PLR0913 - optional maintenance ports
        self,
        repository: RetentionRepository,
        objects: ObjectStore,
        policy: DataRetentionPolicy,
        *,
        composer: ComposerRetentionRepository | None = None,
        composer_content_audit: ComposerContentAuditRepository | None = None,
        composer_connection_audit: ComposerConnectionAuditRepository | None = None,
        composer_model_steps: ComposerModelStepMaintenanceRepository | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._objects = objects
        self._policy = policy
        self._composer = composer
        self._composer_content_audit = composer_content_audit
        self._composer_connection_audit = composer_connection_audit
        self._composer_model_steps = composer_model_steps
        self._clock = clock

    def cleanup(self, *, limit: int) -> int:
        if limit <= 0:
            raise ValueError("Cleanup limit must be positive")
        now = self._clock()
        expired_steps = (
            self._composer_model_steps.recover_stale_model_steps(
                stale_before=now, limit=limit
            )
            if self._composer_model_steps is not None
            else 0
        )
        version_cutoff = now - timedelta(seconds=self._policy.template_version_seconds)
        claims = self._repository.claim_template_versions(
            cutoff_at=version_cutoff,
            now=now,
            lease_expires_at=now + timedelta(seconds=self._policy.claim_lease_seconds),
            minimum_versions=self._policy.minimum_template_versions,
            limit=limit,
        )
        completed = 0
        for claim in claims:
            version = claim.version
            self._objects.delete(
                ObjectKey(
                    ObjectScope.TEMPLATE_VERSION,
                    version.object_owner_id,
                    version.id,
                )
            )
            if self._repository.complete_template_version(claim, completed_at=now):
                completed += 1
        audits = self._repository.cleanup_audits(
            cutoff_at=now - timedelta(seconds=self._policy.audit_seconds),
            completed_at=now,
            limit=limit,
        )
        composer_cleaned = 0
        if (
            self._composer is not None
            and self._policy.composer_draft_seconds is not None
        ):
            composer_cleaned = self._composer.cleanup_expired_drafts(
                cutoff_at=now - timedelta(seconds=self._policy.composer_draft_seconds),
                limit=limit,
            )
            composer_cleaned += self._composer.cleanup_orphan_sources(limit=limit)
        if self._composer_content_audit is not None:
            composer_cleaned += self._composer_content_audit.cleanup_content_audit(
                cutoff_at=now - timedelta(seconds=self._policy.audit_seconds),
                limit=limit,
            )
        if self._composer_connection_audit is not None:
            composer_cleaned += (
                self._composer_connection_audit.cleanup_connection_audit(
                    cutoff_at=now - timedelta(seconds=self._policy.audit_seconds),
                    limit=limit,
                )
            )
        return expired_steps + completed + audits + composer_cleaned
