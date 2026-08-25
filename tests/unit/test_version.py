"""Release-version consistency contracts."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from md_converter import __version__
from md_converter.app import COMPONENT_VERSIONS
from md_converter.version import VERSION

pytestmark = pytest.mark.unit


def test_approved_release_version_is_consistent_across_public_surfaces() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert project["version"] == "0.3"
    assert project["name"] == "markweave"
    assert VERSION == __version__ == "0.3"
    assert ("md-converter", "0.3") in COMPONENT_VERSIONS
    assert "version=VERSION" in Path("src/md_converter/app.py").read_text(
        encoding="utf-8"
    )


def test_release_metadata_declares_public_apache_license() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert (
        Path("LICENSE")
        .read_text(encoding="utf-8")
        .startswith("                                 Apache License\n")
    )
