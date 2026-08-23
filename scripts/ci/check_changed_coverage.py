"""Enforce coverage for changed executable application lines."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
MAX_PERCENT = 100.0


class CoverageCheckError(Exception):
    """Raised when changed-line coverage cannot be evaluated safely."""


@dataclass(frozen=True)
class ChangedCoverage:
    """Changed executable-line coverage totals."""

    covered: int
    executable: int

    @property
    def percent(self) -> float:
        """Return the covered percentage, treating no executable changes as complete."""
        if self.executable == 0:
            return 100.0
        return self.covered * 100 / self.executable


def read_changed_lines(diff: str, *, source_root: PurePosixPath) -> dict[str, set[int]]:
    """Extract added line numbers for Python files below the application source root."""
    changed: dict[str, set[int]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            candidate = line[4:]
            if candidate == "/dev/null" or not candidate.startswith("b/"):
                current = None
                continue
            path = PurePosixPath(candidate.removeprefix("b/"))
            try:
                path.relative_to(source_root)
            except ValueError:
                current = None
                continue
            current = path.as_posix() if path.suffix == ".py" else None
            if current is not None:
                changed.setdefault(current, set())
            continue
        if current is None or not line.startswith("@@ "):
            continue
        match = HUNK_HEADER.match(line)
        if match is None:
            raise CoverageCheckError(f"invalid unified diff hunk: {line}")
        start = int(match.group(1))
        count = int(match.group(2) or "1")
        changed[current].update(range(start, start + count))
    return changed


def load_coverage(path: Path) -> Mapping[str, Mapping[str, Any]]:
    """Load Coverage.py JSON file data with a stable validation error."""
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CoverageCheckError(
            f"cannot read coverage JSON {path}: {error}"
        ) from error
    files = raw.get("files") if isinstance(raw, dict) else None
    if not isinstance(files, dict):
        raise CoverageCheckError("coverage JSON must contain a files object")
    return files


def calculate_changed_coverage(
    changed: Mapping[str, set[int]],
    coverage_files: Mapping[str, Mapping[str, Any]],
) -> ChangedCoverage:
    """Calculate coverage over changed lines that Coverage.py considers executable."""
    covered = 0
    executable = 0
    for path, changed_lines in changed.items():
        file_data = coverage_files.get(path)
        if file_data is None:
            raise CoverageCheckError(
                f"changed application file is absent from coverage: {path}"
            )
        executed_raw = file_data.get("executed_lines")
        missing_raw = file_data.get("missing_lines")
        if (
            not isinstance(executed_raw, list)
            or not all(isinstance(line, int) for line in executed_raw)
            or not isinstance(missing_raw, list)
            or not all(isinstance(line, int) for line in missing_raw)
        ):
            raise CoverageCheckError(f"invalid line coverage data for {path}")
        executed = set(executed_raw)
        missing = set(missing_raw)
        relevant = changed_lines & (executed | missing)
        covered += len(relevant & executed)
        executable += len(relevant)
    return ChangedCoverage(covered=covered, executable=executable)


def git_diff(base: str, head: str, *, repository: Path) -> str:
    """Return a zero-context Python diff without shell interpretation."""
    completed = subprocess.run(
        [
            "git",
            "diff",
            "--unified=0",
            "--no-color",
            "--no-ext-diff",
            f"{base}...{head}",
            "--",
            "*.py",
        ],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "git diff failed"
        raise CoverageCheckError(detail)
    return completed.stdout


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--source-root", type=PurePosixPath, default="src/md_converter")
    parser.add_argument("--fail-under", type=float, default=90.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the changed-line coverage check."""
    args = _parse_args(argv)
    if not 0 <= args.fail_under <= MAX_PERCENT:
        print("error: --fail-under must be between 0 and 100")
        return 2
    try:
        diff = git_diff(args.base, args.head, repository=args.repository)
        changed = read_changed_lines(diff, source_root=args.source_root)
        coverage = calculate_changed_coverage(changed, load_coverage(args.coverage))
    except CoverageCheckError as error:
        print(f"error: {error}")
        return 2
    print(
        "Changed application coverage: "
        f"{coverage.percent:.2f}% ({coverage.covered}/{coverage.executable} lines)"
    )
    return int(coverage.percent < args.fail_under)


if __name__ == "__main__":  # pragma: no cover - exercised by GitHub Actions
    raise SystemExit(main())
