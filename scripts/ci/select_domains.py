"""Select CI domains from changed repository paths."""

from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

COMPOSE_PATTERNS = (
    ".gitignore",
    "README.md",
    "compose.simple.yaml",
    "compose.simple-composer.yaml",
    "compose.podman.yaml",
    "compose.podman-trusted-upstream.yaml",
    "compose.trusted-upstream.yaml",
    "compose.yaml",
    "docs/local-development.md",
    "examples/**",
    "scripts/quickstart-simple.sh",
    "scripts/quickstart.sh",
    "scripts/e2e/run-compose-all.sh",
    "tests/test_quickstart_compose.py",
)

CONTAINER_PATTERNS = (
    "toolchain/document-engines/**",
    "LICENSE",
    "README.md",
    "Containerfile*",
    "**/Containerfile*",
    ".dockerignore",
    ".containerignore",
    "container/**",
    "containers/**",
    "deploy/**",
    "docs/reverse-broker-deployment.md",
    "packaging/broker/**",
    "packaging/systemd/user/markweave-broker.service",
    "spikes/anydoc/LICENSE.anydoc",
    "spikes/anydoc/corpus/**",
    "src/markweave/__init__.py",
    "src/markweave/version.py",
    "src/markweave/conversion/__init__.py",
    "src/markweave/conversion/errors.py",
    "src/markweave/conversion/images.py",
    "src/markweave/cli/commands/recovery.py",
    "src/markweave/cli/commands/local_conversion.py",
    "src/markweave/cli/local_outputs.py",
    "src/markweave/conversion/engine_versions.py",
    "src/markweave/conversion/local.py",
    "src/markweave/conversion/local_settings.py",
    "examples/local-conversion.env",
    "tests/e2e/local-conversion-final-image.sh",
    "tests/e2e/fixtures/local-publication-sync/**",
    "src/markweave/recovery*.py",
    "src/markweave/reversions/**",
    "src/markweave/broker/**",
    "src/markweave/broker_process.py",
    "scripts/container/**",
    "tests/container/**",
    "tests/integration/broker/**",
)

FINAL_IMAGE_E2E_PATTERNS = (
    ".containerignore",
    "Containerfile*",
    "package.json",
    "pnpm-lock.yaml",
    "playwright.config.*",
    "src/**",
    "container/**",
    "scripts/container/**",
    "scripts/e2e/**",
    "scripts/run-web-tests.mjs",
    "toolchain/document-engines/**",
    "tests/e2e/**",
    "tests/corpus/**",
    "containers/**",
    "deploy/**",
)

# Each final-image E2E profile runs as complementary shards; `scripts/e2e/run.sh`
# owns the exhaustive scenario assignment behind `MARKWEAVE_E2E_SHARD`.
E2E_PROFILES = ("standalone", "distributed")
E2E_SHARD_COUNT = 2
E2E_DOMAINS: Mapping[str, tuple[str, int]] = {
    f"e2e-{profile}-{shard}": (profile, shard)
    for profile in E2E_PROFILES
    for shard in range(1, E2E_SHARD_COUNT + 1)
}

# The Compose sub-suites are independent jobs; shared quickstart inputs select
# all of them, and each runner script selects only the jobs that execute it.
COMPOSE_DOMAINS = (
    "compose-podman-insecure",
    "compose-secure",
    "compose-simple-docker",
    "compose-simple-podman",
)

DOMAIN_PATTERNS: Mapping[str, tuple[str, ...]] = {
    "compose-podman-insecure": (
        *COMPOSE_PATTERNS,
        "scripts/e2e/run-compose-podman-insecure.sh",
    ),
    "compose-secure": (*COMPOSE_PATTERNS, "scripts/e2e/run-compose.sh"),
    "compose-simple-docker": (*COMPOSE_PATTERNS, "scripts/e2e/run-compose-simple.sh"),
    "compose-simple-podman": (*COMPOSE_PATTERNS, "scripts/e2e/run-compose-simple.sh"),
    "ci-infrastructure": (
        ".github/ci/**",
        ".github/workflows/**",
        "scripts/ci/**",
        "scripts/testing/**",
        "compose.test.yaml",
        "tests/test_ci_runner.py",
        "tests/test_python_quality.py",
        "tests/integration/ci/**",
    ),
    "container": CONTAINER_PATTERNS,
    "document-engines": (
        "package.json",
        "src/**",
        "scripts/generate_t11_pdf_golden.py",
        "toolchain/document-engines/**",
        "spikes/anydoc/corpus/**",
        "tests/corpus/**",
        "tests/conftest.py",
        "tests/golden/**",
        "tests/integration/anydoc/**",
        "tests/integration/document_engines/**",
        "tests/unit/test_golden_*.py",
    ),
    **dict.fromkeys(E2E_DOMAINS, FINAL_IMAGE_E2E_PATTERNS),
    "frontend": (
        "web/**",
        "openapi/v1.json",
        "scripts/container/build-frontend.sh",
        "scripts/container/ci_images.py",
        "scripts/container/integrity.py",
        "scripts/container/pull-base-images.sh",
    ),
    # The supply-chain scan inspects the same backend and reverse-attempt
    # archives as the container smokes, so it follows the same inputs.
    "supply-chain": CONTAINER_PATTERNS,
}

# Paths that a domain's broad patterns would otherwise match but that its suite
# never executes. The Compose runners are self-contained and run only in the
# Compose domains, so they must not also select both final-image E2E profiles.
DOMAIN_EXCLUSIONS: Mapping[str, tuple[str, ...]] = dict.fromkeys(
    E2E_DOMAINS, ("scripts/e2e/run-compose*.sh",)
)

# Final-image domains consume the images that `CI / build images` builds once per
# run. Each maps to the image roles it downloads, loads, and verifies by ID.
IMAGE_ROLES = ("backend", "frontend", "reverse-attempt")
IMAGE_DOMAIN_ROLES: Mapping[str, tuple[str, ...]] = {
    "container": ("backend", "reverse-attempt"),
    **dict.fromkeys(E2E_DOMAINS, IMAGE_ROLES),
    "frontend": ("frontend",),
    "supply-chain": ("backend", "reverse-attempt"),
}

# Functional, SQLite, PostgreSQL and S3 suites have no domain: the light shards
# (`unit or light_coverage`) and the distributed coverage job run them on every
# pull request, whatever the changed paths, so path selection cannot omit them.

GLOBAL_PATTERNS = (
    ".github/ci/**",
    ".github/workflows/**",
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "scripts/ci/**",
    "scripts/javascript/**",
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
        if any(
            _matches(path, patterns)
            and not _matches(path, DOMAIN_EXCLUSIONS.get(domain, ()))
            for path in normalized
        )
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


def partition_domains(
    runnable: Iterable[str],
) -> tuple[list[str], list[str], list[str]]:
    """Split runnable domains between the shared-image and independent matrices.

    Return the independent heavy domains, the final-image domains that consume
    the images built once per run, and the sorted image roles they require.
    """
    runnable_domains = sorted(set(runnable))
    image_domains = [
        domain for domain in runnable_domains if domain in IMAGE_DOMAIN_ROLES
    ]
    heavy_domains = [
        domain for domain in runnable_domains if domain not in IMAGE_DOMAIN_ROLES
    ]
    roles = sorted(
        {role for domain in image_domains for role in IMAGE_DOMAIN_ROLES[domain]}
    )
    return heavy_domains, image_domains, roles


def write_github_outputs(
    output_path: Path,
    *,
    selected: Sequence[str],
    planned: Sequence[str],
    runnable: Sequence[str],
) -> None:
    """Append compact JSON values to the GitHub Actions output file."""
    heavy, image, roles = partition_domains(runnable)
    values = {
        "selected-domains": list(selected),
        "planned-domains": list(planned),
        "runnable-domains": list(runnable),
        "heavy-domains": heavy,
        "image-domains": image,
        "image-roles": roles,
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
