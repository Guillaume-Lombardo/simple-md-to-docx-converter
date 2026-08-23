"""Run one activated CI domain command without shell interpretation."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, cast

from scripts.ci.select_domains import load_registry

if TYPE_CHECKING:
    from collections.abc import Sequence


def run_domain(domain: str, registry_path: Path) -> int:
    """Run an active domain's reviewed argument vector and return its status."""
    registry = load_registry(registry_path)
    if domain not in registry:
        raise ValueError(f"unknown CI domain: {domain}")
    entry = registry[domain]
    if entry["status"] != "active":
        raise ValueError(f"CI domain is not active: {domain}")
    command = cast("list[str]", entry["command"])
    completed = subprocess.run(command, check=False)
    return completed.returncode


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("domain")
    parser.add_argument(
        "--registry", type=Path, default=Path(".github/ci/domains.json")
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run one domain selected by the workflow matrix."""
    args = _parse_args(argv)
    return run_domain(args.domain, args.registry)


if __name__ == "__main__":  # pragma: no cover - exercised by GitHub Actions
    raise SystemExit(main())
