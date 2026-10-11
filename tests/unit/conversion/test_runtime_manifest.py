"""Recorded conversion component versions must match the shipped toolchain pins."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from markweave.conversion.runtime_manifest import COMPONENT_VERSIONS
from markweave.version import VERSION

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[3]
FINAL_CONTAINERFILE = "Containerfile"
TOOLCHAIN_CONTAINERFILE = "toolchain/document-engines/Containerfile"
MERMAID_MANIFEST = "toolchain/document-engines/package.json"
MERMAID_LOCK = "toolchain/document-engines/package-lock.json"
MERMAID_PACKAGE = "@mermaid-js/mermaid-cli"
SMOKE = "scripts/container/smoke.sh"
CI_WORKFLOW = ".github/workflows/ci.yml"
LIBREOFFICE_ARCHIVE_SCRIPT = "scripts/ci/prepare-libreoffice-archive.sh"


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _only(relative: str, pattern: str) -> str:
    """Return the single captured pin so a moved or duplicated pin fails loudly."""

    matches = set(re.findall(pattern, _text(relative), flags=re.MULTILINE))
    assert len(matches) == 1, f"{relative} must pin exactly one value for {pattern!r}"
    return matches.pop()


def _mermaid_pins() -> dict[str, str]:
    manifest = json.loads(_text(MERMAID_MANIFEST))
    lock = json.loads(_text(MERMAID_LOCK))
    return {
        MERMAID_MANIFEST: manifest["dependencies"][MERMAID_PACKAGE],
        f"{MERMAID_LOCK} root": lock["packages"][""]["dependencies"][MERMAID_PACKAGE],
        f"{MERMAID_LOCK} package": lock["packages"][f"node_modules/{MERMAID_PACKAGE}"][
            "version"
        ],
        SMOKE: _only(SMOKE, r'test "\$\(mmdc --version\)" = "([^"]+)"'),
        CI_WORKFLOW: _only(CI_WORKFLOW, r'/mmdc --version\)" = "([^"]+)"'),
    }


def _pandoc_pins() -> dict[str, str]:
    return {
        FINAL_CONTAINERFILE: _only(FINAL_CONTAINERFILE, r"^ARG PANDOC_VERSION=(\S+)$"),
        TOOLCHAIN_CONTAINERFILE: _only(
            TOOLCHAIN_CONTAINERFILE, r"^ARG PANDOC_VERSION=(\S+)$"
        ),
        SMOKE: _only(SMOKE, r'= "pandoc ([^"]+)"'),
        CI_WORKFLOW: _only(CI_WORKFLOW, r"pandoc/releases/download/([^/]+)/"),
    }


def _chromium_pins() -> dict[str, str]:
    # The Containerfiles pin the RPM version-release; the browser reports the version.
    package = r"^ARG CHROME_VERSION=(\S+)-\d+$"
    return {
        FINAL_CONTAINERFILE: _only(FINAL_CONTAINERFILE, package),
        TOOLCHAIN_CONTAINERFILE: _only(TOOLCHAIN_CONTAINERFILE, package),
        SMOKE: _only(SMOKE, r'= "Google Chrome ([^"]+)"'),
        CI_WORKFLOW: _only(CI_WORKFLOW, r'= "Google Chrome ([^"]+)"'),
    }


def _libreoffice_pins() -> dict[str, str]:
    reported = r'= "LibreOffice (\S+) [0-9a-f]{40}"'
    return {
        SMOKE: _only(SMOKE, reported),
        CI_WORKFLOW: _only(CI_WORKFLOW, reported),
    }


SOURCES = {
    "chromium": _chromium_pins,
    "libreoffice": _libreoffice_pins,
    "mermaid-cli": _mermaid_pins,
    "pandoc": _pandoc_pins,
}


def test_every_recorded_component_has_a_repository_source_of_truth() -> None:
    names = [name for name, _version in COMPONENT_VERSIONS]

    assert names == sorted(set(names))
    assert set(names) == {*SOURCES, "md-converter"}
    assert dict(COMPONENT_VERSIONS)["md-converter"] == VERSION


@pytest.mark.parametrize("component", sorted(SOURCES))
def test_recorded_version_matches_every_shipped_pin(component: str) -> None:
    recorded = dict(COMPONENT_VERSIONS)[component]
    pins = SOURCES[component]()

    assert pins
    assert dict.fromkeys(pins, recorded) == pins


def test_recorded_libreoffice_build_belongs_to_the_pinned_release_archive() -> None:
    # The archive names the release; the executable reports its four-part build.
    recorded = dict(COMPONENT_VERSIONS)["libreoffice"]
    argument = r"^ARG LIBREOFFICE_VERSION=(\S+)$"
    archives = {
        FINAL_CONTAINERFILE: _only(FINAL_CONTAINERFILE, argument),
        f"{FINAL_CONTAINERFILE} archive": _only(
            FINAL_CONTAINERFILE, r"source=LibreOffice_(\S+)_Linux_x86-64_rpm\.tar\.gz"
        ),
        TOOLCHAIN_CONTAINERFILE: _only(TOOLCHAIN_CONTAINERFILE, argument),
        LIBREOFFICE_ARCHIVE_SCRIPT: _only(
            LIBREOFFICE_ARCHIVE_SCRIPT, r"^readonly version=(\S+)$"
        ),
    }

    assert dict.fromkeys(archives, recorded.rpartition(".")[0]) == archives


def test_final_image_smoke_compares_recorded_and_reported_versions() -> None:
    smoke = _text(SMOKE)

    assert (
        "from markweave.conversion.runtime_manifest import COMPONENT_VERSIONS" in smoke
    )
    assert "if recorded != shipped:" in smoke
    for component in (*SOURCES, "md-converter"):
        assert f'"{component}": ' in smoke
