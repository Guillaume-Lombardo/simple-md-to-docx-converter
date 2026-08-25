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
LINE_ARRAY_KEYS = ("executed_lines", "missing_lines", "excluded_lines")
BRANCH_ARRAY_KEYS = ("executed_branches", "missing_branches")
BRANCH_PAIR_SIZE = 2


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


def load_coverage_document(path: Path) -> Mapping[str, Any]:
    """Load a Coverage.py JSON document with a stable validation error."""
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CoverageCheckError(
            f"cannot read coverage JSON {path}: {error}"
        ) from error
    if not isinstance(raw, dict):
        raise CoverageCheckError("coverage JSON must contain an object")
    return raw


def _nonnegative_integer(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoverageCheckError(f"{label} must be a non-negative integer")
    return value


def _line_set(value: object, *, label: str) -> set[int]:
    if not isinstance(value, list) or any(
        isinstance(line, bool) or not isinstance(line, int) or line <= 0
        for line in value
    ):
        raise CoverageCheckError(f"{label} must contain positive integer line numbers")
    lines = set(value)
    if len(lines) != len(value):
        raise CoverageCheckError(f"{label} must not contain duplicate line numbers")
    return lines


def _branch_set(value: object, *, label: str) -> set[tuple[int, int]]:
    if not isinstance(value, list):
        raise CoverageCheckError(f"{label} must contain branch pairs")
    branches: list[tuple[int, int]] = []
    for branch in value:
        if (
            not isinstance(branch, list)
            or len(branch) != BRANCH_PAIR_SIZE
            or any(
                isinstance(line, bool) or not isinstance(line, int) for line in branch
            )
        ):
            raise CoverageCheckError(f"{label} must contain integer branch pairs")
        branches.append((branch[0], branch[1]))
    result = set(branches)
    if len(result) != len(branches):
        raise CoverageCheckError(f"{label} must not contain duplicate branch pairs")
    return result


def validate_file_coverage(path: str, file_data: Mapping[str, Any]) -> None:
    """Validate complete line and branch arrays against a file summary."""
    missing_keys = {
        *LINE_ARRAY_KEYS,
        *BRANCH_ARRAY_KEYS,
        "summary",
        "functions",
        "classes",
    } - file_data.keys()
    if missing_keys:
        raise CoverageCheckError(
            f"incomplete coverage data for {path}: missing {sorted(missing_keys)}"
        )
    if not isinstance(file_data["functions"], dict) or not isinstance(
        file_data["classes"], dict
    ):
        raise CoverageCheckError(f"invalid function or class coverage data for {path}")

    executed = _line_set(file_data["executed_lines"], label=f"{path} executed_lines")
    missing = _line_set(file_data["missing_lines"], label=f"{path} missing_lines")
    excluded = _line_set(file_data["excluded_lines"], label=f"{path} excluded_lines")
    if executed & missing or executed & excluded or missing & excluded:
        raise CoverageCheckError(f"line coverage sets overlap for {path}")

    executed_branches = _branch_set(
        file_data["executed_branches"], label=f"{path} executed_branches"
    )
    missing_branches = _branch_set(
        file_data["missing_branches"], label=f"{path} missing_branches"
    )
    if executed_branches & missing_branches:
        raise CoverageCheckError(f"branch coverage sets overlap for {path}")

    summary = file_data["summary"]
    if not isinstance(summary, dict):
        raise CoverageCheckError(f"coverage summary must be an object for {path}")
    expected = {
        "covered_lines": len(executed),
        "missing_lines": len(missing),
        "excluded_lines": len(excluded),
        "num_statements": len(executed | missing),
        "covered_branches": len(executed_branches),
        "missing_branches": len(missing_branches),
        "num_branches": len(executed_branches | missing_branches),
    }
    for key, count in expected.items():
        actual = _nonnegative_integer(summary.get(key), label=f"{path} summary.{key}")
        if actual != count:
            raise CoverageCheckError(
                f"inconsistent coverage summary for {path}: {key}={actual}, expected {count}"
            )
    partial = _nonnegative_integer(
        summary.get("num_partial_branches"),
        label=f"{path} summary.num_partial_branches",
    )
    if partial > len(missing_branches):
        raise CoverageCheckError(f"invalid partial branch count for {path}")


def load_coverage(path: Path) -> Mapping[str, Mapping[str, Any]]:
    """Load and validate complete per-file Coverage.py JSON data."""
    raw = load_coverage_document(path)
    files = raw.get("files")
    if not isinstance(files, dict):
        raise CoverageCheckError("coverage JSON must contain a files object")
    for file_path, file_data in files.items():
        if not isinstance(file_path, str) or not isinstance(file_data, dict):
            raise CoverageCheckError("coverage files must map paths to objects")
        validate_file_coverage(file_path, file_data)
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
        validate_file_coverage(path, file_data)
        executed = set(file_data["executed_lines"])
        missing = set(file_data["missing_lines"])
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
    parser.add_argument("--source-root", type=PurePosixPath, default="src/markweave")
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
