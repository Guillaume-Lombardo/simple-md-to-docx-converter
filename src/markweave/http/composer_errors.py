"""Composer request preconditions with stable, content-free responses."""


class ComposerPreconditionRequiredError(ValueError):
    """A revision-changing request omitted If-Match."""


class ComposerPreconditionInvalidError(ValueError):
    """An If-Match value did not name one exact revision."""


class ComposerUnavailableError(RuntimeError):
    """The optional Composer model service has no valid operator configuration."""


class ComposerRequestError(ValueError):
    """A Composer source or metadata request failed bounded validation."""


class ComposerRequestTooLargeError(ValueError):
    """A Composer source exceeded the configured bounded upload size."""
