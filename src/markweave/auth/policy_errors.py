"""Idle-session policy concurrency errors."""


class IdleSessionPolicyPreconditionRequiredError(RuntimeError):
    """The update omitted its required revision validator."""


class IdleSessionPolicyConflictError(RuntimeError):
    """The supplied revision no longer identifies current policy state."""


class IdleSessionPolicyAbsoluteLimitError(RuntimeError):
    """A proposed idle duration exceeds the operator absolute lifetime."""
