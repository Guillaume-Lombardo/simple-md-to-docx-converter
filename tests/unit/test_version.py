"""Release-version consistency contracts."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from markweave import __version__
from markweave.app import COMPONENT_VERSIONS
from markweave.version import VERSION

pytestmark = pytest.mark.unit


def test_approved_release_version_is_consistent_across_public_surfaces() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert project["version"] == "0.3.3"
    assert project["name"] == "markweave"
    assert VERSION == __version__ == "0.3.3"
    assert ("md-converter", "0.3.3") in COMPONENT_VERSIONS
    assert "version=VERSION" in Path("src/markweave/app.py").read_text(encoding="utf-8")
    golden_generator = Path("scripts/generate_t11_pdf_golden.py").read_text(
        encoding="utf-8"
    )
    assert "from markweave.version import VERSION" in golden_generator
    assert "application_version=VERSION" in golden_generator


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


def test_release_documentation_uses_only_the_markweave_import() -> None:
    """Release-facing documentation cannot advertise the removed import path."""
    for filename in (
        "README.md",
        "docs/product-specification.md",
        "docs/releasing.md",
    ):
        content = Path(filename).read_text(encoding="utf-8")
        assert "`md_converter`" not in content
        assert "from md_converter import" not in content
