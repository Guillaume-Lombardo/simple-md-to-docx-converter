"""Markdown to DOCX and PDF converter."""

from markweave.app import create_app
from markweave.version import VERSION

__version__ = VERSION

__all__ = ["__version__", "create_app"]
