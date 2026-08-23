"""Select CI domains from changed repository paths."""

from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

DOMAIN_PATTERNS: Mapping[str, tuple[str, ...]] = {
    "ci-infrastructure": (
        ".github/ci/**",
        ".github/workflows/**",
        "scripts/ci/**",
        "tests/test_ci_runner.py",
        "tests/integration/ci/**",
    ),
    "container": (
        "Containerfile*",
        "**/Containerfile*",
        ".dockerignore",
        "containers/**",
        "deploy/**",
    ),
    "document-engines": (
        "src/**",
        "spikes/toolchain/**",
        "tests/corpus/**",
        "tests/integration/document_engines/**",
    ),
    "e2e-distributed": (
        "src/**",
        "tests/e2e/**",
        "containers/**",
        "deploy/**",
    ),
    "e2e-standalone": (
        "src/**",
        "tests/e2e/**",
        "containers/**",
        "deploy/**",
    ),
    "functional": ("src/**", "tests/functional/**"),
    "storage-distributed": (
        "src/**",
        "tests/integration/storage/**",
        "tests/integration/postgres/**",
        "tests/integration/s3/**",
    ),
    "storage-standalone": (
        "src/**",
        "tests/integration/storage/**",
        "tests/integration/sqlite/**",
    ),
}

GLOBAL_PATTERNS = (
    ".github/ci/**",
    ".github/workflows/**",
    "scripts/ci/**",
    "pyproject.toml",
    "uv.lock",
)


def load_registry(path: Path) -> dict[str, dict[str, str | list[str]]]:
    """Load and validate the domain lifecycle registry."""
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != set(DOMAIN_PATTERNS):
        raise ValueError("domain registry must define every known domain exactly once")

    registry: dict[str, dict[str, str | list[str]]] = {}
    for domain, raw_entry in data.items():
        if not isinstance(raw_entry, dict):
            raise TypeError(f"domain {domain!r} must use an object entry")
        status = raw_entry.get("status")
        ticket = raw_entry.get("activation_ticket")
        if status not in {"planned", "active"}:
            raise ValueError(f"domain {domain!r} has invalid status {status!r}")
        if not isinstance(ticket, str) or not ticket.startswith("T"):
            raise ValueError(f"domain {domain!r} requires an activation ticket")
        entry = {"activation_ticket": ticket, "status": status}
        command = raw_entry.get("command")
        if status == "planned" and command is not None:
            raise ValueError(f"planned domain {domain!r} must not declare a command")
        if status == "active":
            if (
                not isinstance(command, list)
                or not command
                or not all(
                    isinstance(argument, str) and argument for argument in command
                )
            ):
                raise ValueError(
                    f"active domain {domain!r} requires a non-empty command array"
                )
            entry["command"] = command
        registry[domain] = entry
    return registry


def _matches(path: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def select_domains(paths: Iterable[str], *, full: bool = False) -> list[str]:
    """Return deterministic domains affected by paths, or every domain in full mode."""
    if full:
        return sorted(DOMAIN_PATTERNS)

    normalized = tuple(path.removeprefix("./") for path in paths if path)
    if any(_matches(path, GLOBAL_PATTERNS) for path in normalized):
        return sorted(DOMAIN_PATTERNS)
    return sorted(
        domain
        for domain, patterns in DOMAIN_PATTERNS.items()
        if any(_matches(path, patterns) for path in normalized)
    )


def classify_domains(
    selected: Iterable[str],
    registry: Mapping[str, Mapping[str, str | list[str]]],
    *,
    draft: bool = False,
) -> tuple[list[str], list[str]]:
    """Separate runnable domains from explicit bootstrap gaps."""
    selected_domains = sorted(selected)
    planned = [
        domain for domain in selected_domains if registry[domain]["status"] == "planned"
    ]
    runnable = [
        domain for domain in selected_domains if registry[domain]["status"] == "active"
    ]
    if draft:
        return planned, []
    return planned, runnable


def write_github_outputs(
    output_path: Path,
    *,
    selected: Sequence[str],
    planned: Sequence[str],
    runnable: Sequence[str],
) -> None:
    """Append compact JSON values to the GitHub Actions output file."""
    values = {
        "selected-domains": list(selected),
        "planned-domains": list(planned),
        "runnable-domains": list(runnable),
    }
    with output_path.open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={json.dumps(value, separators=(',', ':'))}\n")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths-file", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--draft", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run domain selection for GitHub Actions."""
    args = _parse_args(argv)
    paths = args.paths_file.read_bytes().decode("utf-8").split("\0")
    registry = load_registry(args.registry)
    selected = select_domains(paths, full=args.full)
    planned, runnable = classify_domains(selected, registry, draft=args.draft)
    write_github_outputs(
        args.github_output,
        selected=selected,
        planned=planned,
        runnable=runnable,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by GitHub Actions
    raise SystemExit(main())
