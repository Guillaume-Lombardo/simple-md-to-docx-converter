"""Tests for package metadata."""

import tomllib
from importlib import import_module
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path

import pytest

import markweave
from markweave import __version__


@pytest.mark.unit
def test_package_version_matches_distribution_metadata() -> None:
    """The import package and installed distribution expose one version."""
    assert __version__ == version("markweave")
    assert markweave.__all__ == ["__version__"]
    assert not hasattr(markweave, "create_app")
    assert not hasattr(markweave, "VERSION")
    assert find_spec("md_converter") is None


@pytest.mark.unit
def test_internal_runtime_factory_is_not_reexported_as_public_api() -> None:
    """The image can resolve its factory without widening the root package API."""
    assert callable(import_module("markweave.app").create_app)
    assert not hasattr(markweave, "create_app")


@pytest.mark.unit
def test_package_declares_the_markweave_console_entry_point() -> None:
    """The installed executable resolves to the stable root registry."""
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["scripts"] == {"markweave": "markweave.cli.main:main"}


@pytest.mark.unit
def test_distribution_metadata_declares_the_supported_dependency_matrix() -> None:
    """Package metadata keeps the HTTP CLI independent from local service backends."""
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]
    extras = project["optional-dependencies"]

    assert project["dependencies"] == []
    assert extras["standalone"] == ["markweave[server]"]
    assert extras["distributed"] == [
        "markweave[server]",
        "boto3>=1.43.82,<2",
        "psycopg[binary]>=3.3.4,<4",
    ]
    assert extras["all"] == ["markweave[standalone,distributed]"]
    assert extras["reverse-attempt"] == [
        "cairosvg>=2.8,<3",
        "defusedxml>=0.7,<1",
        "firecrawl-anydoc==0.2.4",
        "pillow>=12,<13",
        "tinycss2>=1.5,<2",
    ]
    assert "boto3>=1.43.82,<2" not in extras["server"]
    assert "psycopg[binary]>=3.3.4,<4" not in extras["server"]
    assert "markweave[all,reverse-attempt]" in metadata["dependency-groups"]["dev"]


@pytest.mark.unit
def test_distribution_metadata_is_complete_for_public_pypi_artifacts() -> None:
    """PyPI metadata identifies ownership, support, Python, and Apache licensing."""
    metadata = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    project = metadata["project"]

    assert project["license"] == "Apache-2.0"
    assert project["license-files"] == ["LICENSE"]
    assert project["authors"] == project["maintainers"]
    assert project["keywords"] == ["cli", "conversion", "docx", "markdown", "pdf"]
    assert "Programming Language :: Python :: 3.14" in project["classifiers"]
    assert (
        "License :: OSI Approved :: Apache Software License" in project["classifiers"]
    )
    assert {"Documentation", "Issues", "Repository", "Source", "Support"} <= set(
        project["urls"]
    )
