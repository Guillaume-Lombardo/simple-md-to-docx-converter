"""Validate the exact Cargo component SBOM embedded in the pinned anydoc wheel."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_SHA256 = "ccc492d3014d12a0b5e5cb0a256e130444e6ca910f6c696d53886971ee56f747"
EXPECTED_COMPONENT_COUNT = 113
EXPECTED_DEPENDENCY_COUNT = 114


class AnydocSbomError(ValueError):
    """Raised when the embedded upstream component inventory is not exact."""


def verify_anydoc_cargo_sbom(path: Path) -> None:
    """Require the reviewed anydoc 0.2.4 Cargo graph and license inventory."""

    if path.is_symlink() or not path.is_file():
        raise AnydocSbomError("anydoc Cargo SBOM is unsafe")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != EXPECTED_SHA256:
        raise AnydocSbomError("anydoc Cargo SBOM digest mismatch")
    try:
        document: Any = json.loads(content)
        metadata_component = document["metadata"]["component"]
        components = document["components"]
        dependencies = document["dependencies"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise AnydocSbomError("anydoc Cargo SBOM is malformed") from error
    if (
        not isinstance(metadata_component, dict)
        or metadata_component.get("name") != "anydoc-python"
        or metadata_component.get("version") != "0.2.4"
        or not isinstance(components, list)
        or len(components) != EXPECTED_COMPONENT_COUNT
        or not isinstance(dependencies, list)
        or len(dependencies) != EXPECTED_DEPENDENCY_COUNT
    ):
        raise AnydocSbomError("anydoc Cargo component graph mismatch")

    purls: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise AnydocSbomError("anydoc Cargo component is malformed")
        purl = component.get("purl")
        licenses = component.get("licenses")
        if (
            not isinstance(purl, str)
            or not purl.startswith("pkg:cargo/")
            or purl in purls
            or not isinstance(licenses, list)
            or not licenses
        ):
            raise AnydocSbomError("anydoc Cargo inventory is incomplete")
        purls.add(purl)


def main() -> int:
    """Validate a command-line selected embedded SBOM without rewriting it."""

    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    arguments = parser.parse_args()
    try:
        verify_anydoc_cargo_sbom(arguments.path)
    except (AnydocSbomError, OSError) as error:
        print(f"error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
