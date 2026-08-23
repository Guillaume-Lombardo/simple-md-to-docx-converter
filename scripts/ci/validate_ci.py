"""Perform cheap, repository-local GitHub Actions security checks."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.ci.select_domains import DOMAIN_PATTERNS, load_registry

if TYPE_CHECKING:
    from collections.abc import Iterable

ACTION_REFERENCE = re.compile(
    r"^\s*uses:\s*[^\s@]+@([0-9a-f]+)(?:\s+#.*)?$", re.MULTILINE
)
FULL_SHA = re.compile(r"[0-9a-f]{40}")
TRUSTED_CACHE_WRITE = (
    "save-cache: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' "
    "&& github.repository == 'Guillaume-Lombardo/simple-md-to-docx-converter' }}"
)


def validate_workflow_text(text: str) -> list[str]:
    """Return actionable policy errors for the CI workflow text."""
    errors: list[str] = []
    required_fragments = (
        "pull_request:",
        "merge_group:",
        "push:",
        "release:",
        "workflow_dispatch:",
        "schedule:",
        "permissions:\n  contents: read",
        "concurrency:",
        "name: CI / gate",
        'if [[ "$RUNNABLE_DOMAINS" == "[]" ]]; then',
        '[[ "$HEAVY_RESULT" == "skipped" ]]',
        '[[ "$HEAVY_RESULT" == "success" ]]',
        "uv run pytest -m unit",
        "--cov-report=json:coverage.json",
        "python -m scripts.ci.check_branch_coverage",
        "--coverage coverage.json --fail-under 90",
        "python -m scripts.ci.check_changed_coverage",
        "github.event_name == 'pull_request' || github.event_name == 'merge_group'",
        "--source-root src/md_converter --fail-under 90",
    )
    errors.extend(
        f"missing required workflow fragment: {fragment!r}"
        for fragment in required_fragments
        if fragment not in text
    )
    if text.count("name: CI / gate") != 1:
        errors.append("workflow must define exactly one CI / gate check")

    forbidden = (
        "pull_request_target",
        "secrets.",
        "--privileged",
        "permissions: write",
    )
    errors.extend(
        f"forbidden workflow fragment: {fragment!r}"
        for fragment in forbidden
        if fragment in text
    )
    if re.search(r"^\s+[a-z][a-z-]*:\s+write(?:\s|$)", text, re.MULTILINE):
        errors.append("write permission is forbidden in the CI workflow")

    cache_write_lines = [
        line.strip()
        for line in text.splitlines()
        if line.lstrip().startswith("save-cache:")
    ]
    if cache_write_lines != [TRUSTED_CACHE_WRITE, TRUSTED_CACHE_WRITE]:
        errors.append("cache writes must be limited to trusted pushes on main")

    uses_lines = [
        line for line in text.splitlines() if line.lstrip().startswith("uses:")
    ]
    references = ACTION_REFERENCE.findall(text)
    if len(uses_lines) != len(references):
        errors.append(
            "every action reference must be pinned to a lowercase hexadecimal revision"
        )
    errors.extend(
        f"action revision is not a full commit SHA: {revision!r}"
        for revision in references
        if FULL_SHA.fullmatch(revision) is None
    )

    jobs_text = text.split("\njobs:\n", maxsplit=1)[-1]
    job_count = sum(
        1 for line in jobs_text.splitlines() if re.fullmatch(r"  [a-z][a-z-]*:", line)
    )
    timeout_count = text.count("    timeout-minutes:")
    if timeout_count != job_count:
        errors.append("every job must define a bounded timeout")
    return errors


def validate_python_imports(paths: Iterable[Path]) -> list[str]:
    """Reject direct unittest.mock imports in tracked Python sources."""
    errors: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden = any(
            (
                isinstance(node, ast.ImportFrom)
                and (
                    node.module == "unittest.mock"
                    or (
                        node.module == "unittest"
                        and any(alias.name == "mock" for alias in node.names)
                    )
                )
            )
            or (
                isinstance(node, ast.Import)
                and any(alias.name == "unittest.mock" for alias in node.names)
            )
            for node in ast.walk(tree)
        )
        if forbidden:
            errors.append(f"direct unittest.mock import in {path}")
    return errors


def validate_registry_text(text: str) -> list[str]:
    """Validate registry JSON without trusting it to select a partial domain set."""
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as error:
        return [f"invalid domain registry JSON: {error}"]
    if not isinstance(data, dict) or set(data) != set(DOMAIN_PATTERNS):
        return ["domain registry does not match the selector's known domains"]
    return []


def main() -> int:
    """Validate the committed CI implementation."""
    root = Path(__file__).resolve().parents[2]
    workflow = root / ".github/workflows/ci.yml"
    registry = root / ".github/ci/domains.json"
    errors = validate_workflow_text(workflow.read_text(encoding="utf-8"))
    errors.extend(validate_registry_text(registry.read_text(encoding="utf-8")))
    python_paths = (
        path
        for path in root.rglob("*.py")
        if not {".git", ".venv", "__pycache__"}.intersection(path.parts)
    )
    errors.extend(validate_python_imports(python_paths))
    try:
        load_registry(registry)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        errors.append(str(error))
    for error in errors:
        print(f"error: {error}")
    return bool(errors)


if __name__ == "__main__":  # pragma: no cover - exercised by GitHub Actions
    raise SystemExit(main())
