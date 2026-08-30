"""Tests for package metadata."""

import tomllib
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path

import pytest

from markweave import __version__, create_app


@pytest.mark.unit
def test_package_version_matches_distribution_metadata() -> None:
    """The import package and installed distribution expose one version."""
    assert __version__ == version("markweave")
    assert callable(create_app)
    assert find_spec("md_converter") is None


@pytest.mark.unit
def test_package_declares_the_markweave_console_entry_point() -> None:
    """The installed executable resolves to the stable root registry."""
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["scripts"] == {"markweave": "markweave.cli.main:main"}
