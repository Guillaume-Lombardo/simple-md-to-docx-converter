"""Tests for package metadata."""

from importlib.metadata import version
from importlib.util import find_spec

import pytest

from markweave import __version__, create_app


@pytest.mark.unit
def test_package_version_matches_distribution_metadata() -> None:
    """The import package and installed distribution expose one version."""
    assert __version__ == version("markweave")
    assert callable(create_app)
    assert find_spec("md_converter") is None
