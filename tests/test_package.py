"""Tests for package metadata."""

from importlib.metadata import version

import pytest

import md_converter_server


@pytest.mark.unit
def test_package_version_matches_distribution_metadata() -> None:
    """The import package and installed distribution expose one version."""
    assert md_converter_server.__version__ == version("md-converter-server")
