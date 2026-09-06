"""Stable reverse-job failures without document or infrastructure details."""


class ReversionJobError(RuntimeError):
    """Base class for expected durable reverse-job failures."""


class ReversionJobConflictError(ReversionJobError):
    """An idempotency key or durable transition conflicts."""


class ReversionJobRequestError(ReversionJobError, ValueError):
    """A reverse-job request violates the stable input contract."""


class ReversionJobUserQuotaExceededError(ReversionJobError):
    """The owner already has the configured number of active reverse jobs."""


class ReversionQueueCapacityExceededError(ReversionJobError):
    """The shared persistent queue has reached its configured capacity."""


class ReversionJobLeaseLostError(ReversionJobError):
    """A worker no longer owns the exact reverse attempt and lease."""


class ReversionWorkerInterruptedError(ReversionJobError):
    """The supervisor is shutting down before a terminal transition."""


class ReversionProofRequiredError(ReversionJobError):
    """A broker create intent has no matching durable termination proof."""


class ReversionJobRepositoryError(ReversionJobError):
    """Sanitized reverse queue persistence failure."""
