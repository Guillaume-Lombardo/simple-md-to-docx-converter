"""Markdown to DOCX and PDF converter."""

from markweave.version import VERSION

__version__ = VERSION

__all__ = ["__version__", "create_app"]


def __getattr__(name: str) -> object:
    """Load the optional server factory only when public callers request it."""
    if name == "create_app":
        from markweave.app import create_app  # noqa: PLC0415 - intentional lazy import

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
