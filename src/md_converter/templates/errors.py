"""Stable template-domain failures independent of HTTP delivery."""


class TemplateUnavailableError(LookupError):
    """A requested identity is missing, inactive, or invisible."""

    def __init__(self) -> None:
        super().__init__("Template is not available")
