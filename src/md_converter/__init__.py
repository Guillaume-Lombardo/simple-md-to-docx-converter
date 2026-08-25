"""Markdown to DOCX and PDF converter."""

from md_converter.app import create_app
from md_converter.version import VERSION

__version__ = VERSION

__all__ = ["__version__", "create_app"]
