"""Composed SQL implementation of the provider-neutral job ports."""

from markweave.persistence.jobs.claims import _JobClaimRepository
from markweave.persistence.jobs.cleanup import _JobCleanupRepository
from markweave.persistence.jobs.lifecycle import _JobTerminalRepository
from markweave.persistence.jobs.queries import _JobQueryRepository
from markweave.persistence.jobs.submission import _JobSubmissionRepository


class SqlJobRepository(
    _JobSubmissionRepository,
    _JobQueryRepository,
    _JobClaimRepository,
    _JobTerminalRepository,
    _JobCleanupRepository,
):
    """Atomic job repository composed from responsibility-bounded stores."""
