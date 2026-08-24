"""Stable job-domain failures without document or infrastructure details."""


class JobError(RuntimeError):
    """Base class for expected job workflow failures."""


class JobNotFoundError(JobError, LookupError):
    """A visible job does not exist."""


class JobConflictError(JobError):
    """An idempotency key or state transition conflicts."""


class JobRequestError(JobError, ValueError):
    """A conversion request violates the stable input contract."""


class JobLeaseLostError(JobError):
    """A worker no longer owns the claimed job lease."""


class JobRepositoryError(JobError):
    """Sanitized persistent queue failure."""


class JobProcessingCancelled(JobError):
    """The processor observed a durable cancellation request."""
