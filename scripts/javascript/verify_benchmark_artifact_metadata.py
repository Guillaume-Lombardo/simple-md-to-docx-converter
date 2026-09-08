"""Verify the exact immutable GitHub Actions artifact accepted for T67."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

REPOSITORY = "Guillaume-Lombardo/simple-md-to-docx-converter"
REPOSITORY_ID = 1_343_515_292
RUN_ID = 33_799_673_333
RUN_ATTEMPT = 1
ARTIFACT_ID = 9_911_803_951
ARTIFACT_NAME = "package-manager-benchmark-33799673333-1"
ARTIFACT_DIGEST = (
    "sha256:90311dccb8db14a017050120f84379ba61b96ba69a6dccf5a379c2a2a4e48a0c"
)
HEAD_SHA = "da26ad780ac11d099e764aa82a0430e684bbf4c3"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}"
RECEIPT_LINES = (
    f"artifact_id={ARTIFACT_ID}",
    f"artifact_name={ARTIFACT_NAME}",
    f"artifact_digest={ARTIFACT_DIGEST}",
    f"run_id={RUN_ID}",
    f"run_attempt={RUN_ATTEMPT}",
    "run_status=completed",
    "run_conclusion=success",
    f"run_head_sha={HEAD_SHA}",
    f"repository_id={REPOSITORY_ID}",
    f"repository={REPOSITORY}",
    f"head_repository_id={REPOSITORY_ID}",
    f"head_repository={REPOSITORY}",
)


class MetadataError(RuntimeError):
    """Accepted artifact provenance does not match the immutable contract."""


def _fetch_json(url: str, token: str) -> dict[str, Any]:
    request = Request(  # noqa: S310 - fixed GitHub API origin and repository
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed URL above
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise MetadataError("GitHub returned malformed artifact metadata")
    return payload


def _require_equal(actual: object, expected: object, field: str) -> None:
    if actual != expected:
        raise MetadataError(f"accepted benchmark metadata mismatch: {field}")


def _validate(artifact: dict[str, Any], run: dict[str, Any]) -> None:
    artifact_run = artifact.get("workflow_run")
    repository = run.get("repository")
    head_repository = run.get("head_repository")
    if not isinstance(artifact_run, dict):
        raise MetadataError("accepted benchmark artifact has no workflow run")
    if not isinstance(repository, dict) or not isinstance(head_repository, dict):
        raise MetadataError("accepted benchmark run has no repository identity")
    _require_equal(artifact.get("id"), ARTIFACT_ID, "artifact id")
    _require_equal(artifact.get("name"), ARTIFACT_NAME, "artifact name")
    _require_equal(artifact.get("digest"), ARTIFACT_DIGEST, "artifact digest")
    _require_equal(artifact.get("expired"), False, "artifact expiration")
    _require_equal(artifact_run.get("id"), RUN_ID, "artifact run id")
    _require_equal(
        artifact_run.get("repository_id"), REPOSITORY_ID, "artifact repository id"
    )
    _require_equal(
        artifact_run.get("head_repository_id"),
        REPOSITORY_ID,
        "artifact head repository id",
    )
    _require_equal(artifact_run.get("head_sha"), HEAD_SHA, "artifact head SHA")
    _require_equal(run.get("id"), RUN_ID, "run id")
    _require_equal(run.get("run_attempt"), RUN_ATTEMPT, "run attempt")
    _require_equal(run.get("status"), "completed", "run status")
    _require_equal(run.get("conclusion"), "success", "run conclusion")
    _require_equal(run.get("head_sha"), HEAD_SHA, "run head SHA")
    _require_equal(repository.get("id"), REPOSITORY_ID, "repository id")
    _require_equal(repository.get("full_name"), REPOSITORY, "repository name")
    _require_equal(head_repository.get("id"), REPOSITORY_ID, "head repository id")
    _require_equal(head_repository.get("full_name"), REPOSITORY, "head repository name")


def main(arguments: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if arguments is None else arguments)
    if len(values) != 1 or not values[0]:
        print("Usage: verify_benchmark_artifact_metadata.py RECEIPT", file=sys.stderr)
        return 2
    receipt = Path(values[0])
    receipt.unlink(missing_ok=True)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "GITHUB_TOKEN is required for artifact metadata verification",
            file=sys.stderr,
        )
        return 2
    try:
        artifact = _fetch_json(f"{API_ROOT}/actions/artifacts/{ARTIFACT_ID}", token)
        run = _fetch_json(f"{API_ROOT}/actions/runs/{RUN_ID}", token)
        _validate(artifact, run)
        receipt.write_text("\n".join(RECEIPT_LINES) + "\n", encoding="utf-8")
    except (MetadataError, OSError, json.JSONDecodeError) as error:
        print(f"T67 benchmark metadata verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
