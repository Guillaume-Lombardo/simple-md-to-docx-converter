#!/usr/bin/env python3
"""Select, run, and attest the reviewed critical mutation campaign."""

from __future__ import annotations

import argparse
import fnmatch
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "mutation/domains.json"
DEFAULT_ARTIFACT = ROOT / "mutation-results/report.json"
FAILURE_STATUSES = (
    "survived",
    "no_tests",
    "suspicious",
    "timeout",
    "check_was_interrupted_by_user",
    "segfault",
)


@dataclass(frozen=True)
class MutationDomain:
    """One reviewed, risk-ranked group of exact mutants."""

    name: str
    priority: int
    paths: tuple[str, ...]
    mutants: tuple[str, ...]
    review_notes: tuple[str, ...]


@dataclass(frozen=True)
class MutationManifest:
    """Validated campaign configuration."""

    schema_version: int
    always_paths: tuple[str, ...]
    failure_statuses: tuple[str, ...]
    domains: tuple[MutationDomain, ...]


def _string_tuple(
    value: object, *, field: str, nonempty: bool = True
) -> tuple[str, ...]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{field} must be a{' non-empty' if nonempty else ''} list")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{field} must contain non-empty strings")
    return tuple(value)


def load_manifest(path: Path = DEFAULT_MANIFEST) -> MutationManifest:
    """Load and strictly validate the reviewed campaign manifest."""

    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "always_paths",
        "failure_statuses",
        "domains",
    }:
        raise ValueError("mutation manifest has unexpected top-level fields")
    if raw["schema_version"] != 1:
        raise ValueError("unsupported mutation manifest schema")
    failure_statuses = _string_tuple(raw["failure_statuses"], field="failure_statuses")
    if failure_statuses != FAILURE_STATUSES:
        raise ValueError("failure_statuses must match the reviewed strict status set")
    domains_raw = raw["domains"]
    if not isinstance(domains_raw, list) or not domains_raw:
        raise ValueError("domains must be a non-empty list")
    domains: list[MutationDomain] = []
    names: set[str] = set()
    mutants: set[str] = set()
    for index, value in enumerate(domains_raw):
        field = f"domains[{index}]"
        if not isinstance(value, dict) or set(value) != {
            "name",
            "priority",
            "paths",
            "mutants",
            "review_notes",
        }:
            raise ValueError(f"{field} has unexpected fields")
        name = value["name"]
        priority = value["priority"]
        if not isinstance(name, str) or not name or name in names:
            raise ValueError(f"{field}.name must be unique and non-empty")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority <= 0:
            raise ValueError(f"{field}.priority must be a positive integer")
        domain_mutants = _string_tuple(value["mutants"], field=f"{field}.mutants")
        duplicates = mutants.intersection(domain_mutants)
        if duplicates:
            raise ValueError(f"mutants must be unique: {sorted(duplicates)}")
        names.add(name)
        mutants.update(domain_mutants)
        domains.append(
            MutationDomain(
                name=name,
                priority=priority,
                paths=_string_tuple(value["paths"], field=f"{field}.paths"),
                mutants=domain_mutants,
                review_notes=_string_tuple(
                    value["review_notes"], field=f"{field}.review_notes"
                ),
            )
        )
    if domains != sorted(domains, key=lambda domain: (domain.priority, domain.name)):
        raise ValueError("domains must be ordered by priority and name")
    return MutationManifest(
        schema_version=1,
        always_paths=_string_tuple(raw["always_paths"], field="always_paths"),
        failure_statuses=failure_statuses,
        domains=tuple(domains),
    )


def select_domains(
    manifest: MutationManifest,
    *,
    mode: str,
    changed_paths: tuple[str, ...] = (),
) -> tuple[MutationDomain, ...]:
    """Select every domain, one named domain, or domains affected by a diff."""

    if mode == "all":
        return manifest.domains
    by_name = {domain.name: domain for domain in manifest.domains}
    if mode in by_name:
        return (by_name[mode],)
    if mode != "changed":
        raise ValueError(f"unknown mutation mode: {mode}")
    if any(
        fnmatch.fnmatchcase(path, pattern)
        for path in changed_paths
        for pattern in manifest.always_paths
    ):
        return manifest.domains
    return tuple(
        domain
        for domain in manifest.domains
        if any(
            fnmatch.fnmatchcase(path, pattern)
            for path in changed_paths
            for pattern in domain.paths
        )
    )


def changed_paths(
    base_sha: str, head_sha: str, *, target_root: Path = ROOT
) -> tuple[str, ...]:
    """Return repository-relative changed paths for an immutable commit range."""

    if not base_sha or not head_sha:
        raise ValueError("changed mode requires non-empty base and head SHAs")
    result = subprocess.run(
        [
            "git",
            "diff",
            "--name-status",
            "-z",
            "--diff-filter=ACDMRT",
            base_sha,
            head_sha,
        ],
        cwd=target_root,
        check=True,
        capture_output=True,
        text=True,
    )
    fields = result.stdout.split("\0")
    if fields[-1] != "":
        raise RuntimeError("git diff name-status output is not NUL-terminated")
    fields.pop()
    paths: set[str] = set()
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        path_count = 2 if status[:1] in {"R", "C"} else 1
        if not status or index + path_count > len(fields):
            raise RuntimeError("git diff name-status output is malformed")
        for raw_path in fields[index : index + path_count]:
            path = PurePosixPath(raw_path)
            if not raw_path or path.is_absolute() or ".." in path.parts:
                raise RuntimeError("git diff returned an unsafe repository path")
            paths.add(path.as_posix())
        index += path_count
    return tuple(sorted(paths))


def verify_stats(stats: dict[str, Any], *, selected: int) -> dict[str, int]:
    """Require exact killed/selected equality and no non-killed terminal status."""

    if selected <= 0:
        raise ValueError("a mutation run must select at least one mutant")
    counts: dict[str, int] = {}
    for name in ("killed", *FAILURE_STATUSES):
        value = stats.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"mutation statistic {name!r} must be a non-negative integer"
            )
        counts[name] = value
    failures = {name: counts[name] for name in FAILURE_STATUSES if counts[name]}
    if failures:
        raise ValueError(f"mutation campaign left non-killed mutants: {failures}")
    if counts["killed"] != selected:
        raise ValueError(
            f"mutation campaign killed {counts['killed']} of {selected} selected mutants"
        )
    return counts


def _write_artifact(
    path: Path,
    *,
    mode: str,
    domains: tuple[MutationDomain, ...],
    changed: tuple[str, ...],
    status: str,
    counts: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    selected = sum(len(domain.mutants) for domain in domains)
    document = {
        "schema_version": 1,
        "mode": mode,
        "status": status,
        "selected": selected,
        "killed": counts["killed"] if counts else 0,
        "failure_statuses": {
            name: counts[name] if counts else 0 for name in FAILURE_STATUSES
        },
        "changed_paths": list(changed),
        "domains": [
            {
                "name": domain.name,
                "priority": domain.priority,
                "selected": len(domain.mutants),
                "command": ["mutmut", "run", *domain.mutants],
                "mutants": list(domain.mutants),
                "review_notes": list(domain.review_notes),
            }
            for domain in domains
        ],
        "error": error,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _run_mutmut(
    domains: tuple[MutationDomain, ...], *, target_root: Path = ROOT
) -> dict[str, Any]:
    executable = shutil.which("mutmut")
    if executable is None:
        raise RuntimeError("mutmut executable is unavailable")
    generated = target_root / "mutants"
    if generated.is_symlink() or (generated.exists() and not generated.is_dir()):
        raise RuntimeError("refusing to remove an unexpected mutants path")
    if generated.is_dir():
        shutil.rmtree(generated)
    mutants = [mutant for domain in domains for mutant in domain.mutants]
    run = subprocess.run([executable, "run", *mutants], cwd=target_root, check=False)
    exported = subprocess.run(
        [executable, "export-cicd-stats"], cwd=target_root, check=False
    )
    stats_path = generated / "mutmut-cicd-stats.json"
    if exported.returncode != 0 or not stats_path.is_file():
        raise RuntimeError("mutmut did not export CI statistics")
    stats: Any = json.loads(stats_path.read_text(encoding="utf-8"))
    if not isinstance(stats, dict):
        raise RuntimeError("mutmut exported malformed CI statistics")
    if run.returncode not in {0, 1}:
        raise RuntimeError(
            f"mutmut terminated unexpectedly with status {run.returncode}"
        )
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--base-sha", default="")
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--target-root", type=Path, default=ROOT)
    parser.add_argument("--plan-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    domains: tuple[MutationDomain, ...] = ()
    changed: tuple[str, ...] = ()
    try:
        target_root = args.target_root.resolve()
        if not target_root.is_dir():
            raise ValueError("mutation target root must be an existing directory")
        manifest = load_manifest(args.manifest)
        changed = (
            changed_paths(args.base_sha, args.head_sha, target_root=target_root)
            if args.mode == "changed"
            else ()
        )
        domains = select_domains(manifest, mode=args.mode, changed_paths=changed)
        if not domains:
            _write_artifact(
                args.artifact,
                mode=args.mode,
                domains=(),
                changed=changed,
                status="not-affected",
            )
            print("No reviewed mutation domain is affected.")
            return 0
        if args.plan_only:
            _write_artifact(
                args.artifact,
                mode=args.mode,
                domains=domains,
                changed=changed,
                status="planned",
            )
            print(
                f"Planned {sum(len(domain.mutants) for domain in domains)} mutants "
                f"across {len(domains)} domains."
            )
            return 0
        stats = _run_mutmut(domains, target_root=target_root)
        selected = sum(len(domain.mutants) for domain in domains)
        counts = verify_stats(stats, selected=selected)
        _write_artifact(
            args.artifact,
            mode=args.mode,
            domains=domains,
            changed=changed,
            status="passed",
            counts=counts,
        )
        print(f"Mutation campaign killed all {selected} selected mutants.")
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        _write_artifact(
            args.artifact,
            mode=args.mode,
            domains=domains,
            changed=changed,
            status="failed",
            error=str(error),
        )
        print(f"Mutation campaign failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
