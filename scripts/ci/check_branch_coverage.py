"""Enforce an application branch-only coverage ratio from Coverage.py JSON."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.ci.check_changed_coverage import (
    CoverageCheckError,
    load_coverage,
    load_coverage_document,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

MAX_PERCENT = 100.0


@dataclass(frozen=True)
class BranchCoverage:
    """Application branch coverage totals."""

    covered: int
    branches: int

    @property
    def percent(self) -> float:
        """Return 100% when valid instrumented application code has no branches."""
        if self.branches == 0:
            return 100.0
        return self.covered * 100 / self.branches


def _count(totals: Mapping[str, Any], key: str) -> int:
    value = totals.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CoverageCheckError(
            f"coverage totals.{key} must be a non-negative integer"
        )
    return value


def calculate_branch_coverage(totals: Mapping[str, Any]) -> BranchCoverage:
    """Validate branch totals and return their independent coverage ratio."""
    branches = _count(totals, "num_branches")
    covered = _count(totals, "covered_branches")
    missing = _count(totals, "missing_branches")
    partial = _count(totals, "num_partial_branches")
    if covered + missing != branches:
        raise CoverageCheckError("covered and missing branches must equal num_branches")
    if partial > missing:
        raise CoverageCheckError("partial branches cannot exceed missing branches")
    return BranchCoverage(covered=covered, branches=branches)


def validate_branch_report(path: Path) -> BranchCoverage:
    """Validate report metadata, file schemas, and aggregate branch totals."""
    document = load_coverage_document(path)
    meta = document.get("meta")
    if not isinstance(meta, dict) or meta.get("branch_coverage") is not True:
        raise CoverageCheckError("coverage JSON must enable branch coverage")
    totals = document.get("totals")
    if not isinstance(totals, dict):
        raise CoverageCheckError("coverage JSON must contain a totals object")
    files = load_coverage(path)
    coverage = calculate_branch_coverage(totals)
    file_branches = sum(
        file_data["summary"]["num_branches"] for file_data in files.values()
    )
    file_covered = sum(
        file_data["summary"]["covered_branches"] for file_data in files.values()
    )
    file_missing = sum(
        file_data["summary"]["missing_branches"] for file_data in files.values()
    )
    file_partial = sum(
        file_data["summary"]["num_partial_branches"] for file_data in files.values()
    )
    total_partial = _count(totals, "num_partial_branches")
    if (
        coverage.branches,
        coverage.covered,
        coverage.branches - coverage.covered,
        total_partial,
    ) != (
        file_branches,
        file_covered,
        file_missing,
        file_partial,
    ):
        raise CoverageCheckError(
            "aggregate branch totals do not match per-file coverage"
        )
    return coverage


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--fail-under", type=float, default=90.0)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run branch-only coverage enforcement."""
    args = _parse_args(argv)
    if not 0 <= args.fail_under <= MAX_PERCENT:
        print("error: --fail-under must be between 0 and 100")
        return 2
    try:
        coverage = validate_branch_report(args.coverage)
    except CoverageCheckError as error:
        print(f"error: {error}")
        return 2
    print(
        "Application branch coverage: "
        f"{coverage.percent:.2f}% ({coverage.covered}/{coverage.branches} branches)"
    )
    return int(coverage.percent < args.fail_under)


if __name__ == "__main__":  # pragma: no cover - exercised by Pytest and CI
    raise SystemExit(main())
