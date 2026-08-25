"""Perform cheap, repository-local GitHub Actions security checks."""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver

from scripts.ci.select_domains import DOMAIN_PATTERNS, load_registry

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

ACTION_REFERENCE = re.compile(
    r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)@([0-9a-f]+)$"
)
FULL_SHA = re.compile(r"[0-9a-f]{40}")
TRUSTED_REPOSITORY = "Guillaume-Lombardo/simple-md-to-docx-converter"
TRUSTED_REPOSITORY_CONDITION = f"github.repository == '{TRUSTED_REPOSITORY}'"
TRUSTED_REPOSITORY_GUARD = f"${{{{ {TRUSTED_REPOSITORY_CONDITION} }}}}"
TRUSTED_CACHE_WRITE = (
    "save-cache: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' "
    "&& github.repository == 'Guillaume-Lombardo/simple-md-to-docx-converter' }}"
)
READ_ONLY_PERMISSIONS = {"contents": "read"}
FORBIDDEN_WORKFLOW_FRAGMENTS = (
    "pull_request_target",
    "repository_dispatch",
    "workflow_run",
    "secrets:",
    "--privileged",
)
FORBIDDEN_EXPRESSION_PATTERNS = (
    (re.compile(r"\bsecrets\s*(?:\.|\[)", re.IGNORECASE), "secret access"),
    (
        re.compile(
            r"\bgithub\s*(?:\.\s*token\b|\[\s*(['\"])token\1\s*\])",
            re.IGNORECASE,
        ),
        "GitHub token access",
    ),
)
RELEASE_FORBIDDEN_TRIGGERS = frozenset(
    {"pull_request", "pull_request_target", "merge_group", "workflow_dispatch"}
)
RELEASE_TRIGGER_CANDIDATES = frozenset({"push", "release"})
MAX_RELEASE_TIMEOUT_MINUTES = 60
PUBLISH_STEP_COUNT = 2
RELEASE_BUILD_COMMAND = "uv run python -m scripts.release.build --output dist"


@dataclass(frozen=True)
class WorkflowPolicy:
    """Allowlisted shape for one committed read-only workflow."""

    triggers: frozenset[str]
    jobs: Mapping[str, int]
    actions: frozenset[str]
    concurrency_group: str
    cancel_in_progress: bool | str


READ_ONLY_WORKFLOW_POLICIES = {
    "ci.yml": WorkflowPolicy(
        triggers=frozenset(
            {
                "pull_request",
                "merge_group",
                "push",
                "release",
                "workflow_dispatch",
                "schedule",
            }
        ),
        jobs={"detect": 5, "light": 15, "domain-plan": 5, "heavy": 45, "gate": 5},
        actions=frozenset(
            {
                "actions/checkout",
                "actions/setup-node",
                "actions/setup-python",
                "actions/upload-artifact",
                "astral-sh/setup-uv",
            }
        ),
        concurrency_group=(
            "ci-${{ github.event.pull_request.number || "
            "github.event.merge_group.head_sha || github.ref }}"
        ),
        cancel_in_progress=(
            "${{ github.event_name == 'pull_request' || "
            "github.event_name == 'merge_group' }}"
        ),
    ),
    "mutation.yml": WorkflowPolicy(
        triggers=frozenset({"schedule", "workflow_dispatch"}),
        jobs={"mutation": 30},
        actions=frozenset(
            {"actions/checkout", "actions/setup-python", "astral-sh/setup-uv"}
        ),
        concurrency_group="mutation-${{ github.ref }}",
        cancel_in_progress=True,
    ),
}


class WorkflowLoader(yaml.SafeLoader):
    """Load GitHub workflow YAML using YAML 1.2 booleans and unique keys."""


WorkflowLoader.yaml_implicit_resolvers = {
    key: [
        (tag, pattern) for tag, pattern in resolvers if tag != "tag:yaml.org,2002:bool"
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
WorkflowLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def _construct_unique_mapping(
    loader: WorkflowLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


WorkflowLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _load_workflow(text: str) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        loaded: Any = yaml.load(
            text,
            Loader=WorkflowLoader,  # noqa: S506 - no Python object constructors
        )
    except yaml.YAMLError as error:
        return None, [f"invalid workflow YAML: {error}"]
    if not isinstance(loaded, dict):
        return None, ["workflow root must be a mapping"]
    if not all(isinstance(key, str) for key in loaded):
        return None, ["workflow root keys must be strings"]
    return loaded, []


def _mapping(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return value


def _trigger_names(workflow: Mapping[str, Any]) -> set[str] | None:
    triggers = _mapping(workflow.get("on"))
    return set(triggers) if triggers is not None else None


def _action_references(workflow: object) -> list[object]:
    references: list[object] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key == "uses":
                    references.append(child)
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(workflow)
    return references


def _validate_lexical_security(text: str) -> list[str]:
    errors = [
        f"forbidden workflow fragment: {fragment!r}"
        for fragment in FORBIDDEN_WORKFLOW_FRAGMENTS
        if fragment.lower() in text.lower()
    ]
    errors.extend(
        f"forbidden workflow {description}"
        for pattern, description in FORBIDDEN_EXPRESSION_PATTERNS
        if pattern.search(text)
    )
    return errors


def _validate_concurrency(
    workflow: Mapping[str, Any],
    *,
    expected_group: str | None = None,
    expected_prefix: str | None = None,
    expected_cancellation: bool | str | None = None,
) -> list[str]:
    concurrency = _mapping(workflow.get("concurrency"))
    if concurrency is None or set(concurrency) != {"group", "cancel-in-progress"}:
        return [
            "workflow must define explicit group and cancel-in-progress concurrency"
        ]
    group = concurrency["group"]
    cancellation = concurrency["cancel-in-progress"]
    errors: list[str] = []
    if not isinstance(group, str) or not group.strip():
        errors.append("workflow concurrency group must be a non-empty string")
    elif expected_group is not None and group != expected_group:
        errors.append("workflow concurrency group does not match the explicit policy")
    elif expected_prefix is not None and not group.startswith(expected_prefix):
        errors.append(f"workflow concurrency group must start with {expected_prefix!r}")
    if expected_cancellation is not None and cancellation != expected_cancellation:
        errors.append("workflow cancel-in-progress does not match the explicit policy")
    elif not isinstance(cancellation, (bool, str)):
        errors.append("workflow cancel-in-progress must be explicit")
    return errors


def _validate_jobs(workflow: Mapping[str, Any], *, policy: WorkflowPolicy) -> list[str]:
    jobs = _mapping(workflow.get("jobs"))
    if jobs is None:
        return ["workflow jobs must be a mapping"]
    errors: list[str] = []
    invalid_timeout = False
    if set(jobs) != set(policy.jobs):
        errors.append("workflow jobs do not match the explicit allowlist")
    for job_name, job_value in jobs.items():
        job = _mapping(job_value)
        if job is None:
            errors.append(f"job {job_name!r} must be a mapping")
            continue
        timeout = job.get("timeout-minutes")
        maximum = policy.jobs.get(job_name)
        if (
            not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or timeout <= 0
            or maximum is None
            or timeout > maximum
        ):
            invalid_timeout = True
            errors.append(
                f"job {job_name!r} must define an allowlisted bounded timeout"
            )
        if "permissions" in job:
            errors.append(
                f"read-only job {job_name!r} must not override workflow permissions"
            )
        if job.get("runs-on") != "ubuntu-24.04":
            errors.append(f"job {job_name!r} must use the allowlisted hosted runner")
    if invalid_timeout:
        errors.append("every job must define a bounded timeout")
    return errors


def _validate_action_allowlist(
    workflow: Mapping[str, Any], *, allowed_actions: frozenset[str]
) -> list[str]:
    errors: list[str] = []
    for reference in _action_references(workflow):
        if not isinstance(reference, str):
            errors.append("every action reference must be a string")
            continue
        match = ACTION_REFERENCE.fullmatch(reference)
        if match is None:
            errors.append(
                "every action reference must be pinned to a lowercase hexadecimal revision"
            )
            continue
        action, revision = match.groups()
        if FULL_SHA.fullmatch(revision) is None:
            errors.append(f"action revision is not a full commit SHA: {revision!r}")
            continue
        if action not in allowed_actions:
            errors.append(f"action is not allowlisted for this workflow: {reference!r}")
    return errors


def _validate_checkout_credentials(workflow: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    jobs = _mapping(workflow.get("jobs")) or {}
    for job_name, job_value in jobs.items():
        job = _mapping(job_value) or {}
        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            step_mapping = _mapping(step) or {}
            uses = step_mapping.get("uses")
            if not isinstance(uses, str) or not uses.startswith("actions/checkout@"):
                continue
            options = _mapping(step_mapping.get("with")) or {}
            if options.get("persist-credentials") is not False:
                errors.append(
                    f"checkout in job {job_name!r} must disable persisted credentials"
                )
    return errors


def _validate_read_only_workflow(
    workflow: Mapping[str, Any], *, policy: WorkflowPolicy
) -> list[str]:
    errors: list[str] = []
    triggers = _trigger_names(workflow)
    if triggers != set(policy.triggers):
        errors.append("workflow triggers do not match the explicit allowlist")
    if workflow.get("permissions") != READ_ONLY_PERMISSIONS:
        errors.append("read-only workflow permissions must be exactly contents: read")
    errors.extend(
        _validate_concurrency(
            workflow,
            expected_group=policy.concurrency_group,
            expected_cancellation=policy.cancel_in_progress,
        )
    )
    errors.extend(_validate_jobs(workflow, policy=policy))
    errors.extend(_validate_action_allowlist(workflow, allowed_actions=policy.actions))
    errors.extend(_validate_checkout_credentials(workflow))
    return errors


def _normalized_command(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return " ".join(value.split())


def _job_steps(workflow: Mapping[str, Any], job_name: str) -> list[dict[str, Any]]:
    jobs = _mapping(workflow.get("jobs")) or {}
    job = _mapping(jobs.get(job_name)) or {}
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    return [step for value in steps if (step := _mapping(value)) is not None]


def _validate_ci_contract(workflow: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    jobs = _mapping(workflow.get("jobs")) or {}
    if (
        sum((_mapping(job) or {}).get("name") == "CI / gate" for job in jobs.values())
        != 1
    ):
        errors.append("workflow must define exactly one CI / gate check")

    required_commands = {
        ("light", "Run unit tests with branch coverage"): (
            "uv run pytest -m unit --cov-report=json:coverage.json"
        ),
        ("light", "Enforce application branch-only coverage"): (
            "uv run python -m scripts.ci.check_branch_coverage "
            "--coverage coverage.json --fail-under 90"
        ),
        ("light", "Enforce changed application line coverage"): (
            "uv run python -m scripts.ci.check_changed_coverage "
            '--base "$BASE_SHA" --head "$HEAD_SHA" --coverage coverage.json '
            "--source-root src/md_converter --fail-under 90"
        ),
        ("heavy", "Run authenticated conversion workflow in pinned Chrome"): (
            "npm run test:web-browser"
        ),
        ("heavy", "Install the locked E2E browser driver"): ("npm ci --ignore-scripts"),
        ("gate", "Require every implemented CI stage"): (
            'set -euo pipefail [[ "$DETECT_RESULT" == "success" ]] '
            '[[ "$DOMAIN_PLAN_RESULT" == "success" ]] '
            'if [[ "$RUNNABLE_DOMAINS" == "[]" ]]; then '
            '[[ "$HEAVY_RESULT" == "skipped" ]] else '
            '[[ "$HEAVY_RESULT" == "success" ]] fi '
            '[[ "$LIGHT_RESULT" == "success" ]]'
        ),
    }
    for (job_name, step_name), expected_command in required_commands.items():
        matches = [
            step
            for step in _job_steps(workflow, job_name)
            if step.get("name") == step_name
        ]
        if (
            len(matches) != 1
            or _normalized_command(matches[0].get("run")) != expected_command
        ):
            errors.append(f"missing required workflow command: {expected_command!r}")

    required_conditions = {
        ("light", "Enforce changed application line coverage"): (
            "${{ github.event_name == 'pull_request' || "
            "github.event_name == 'merge_group' }}"
        ),
        ("heavy", "Install rootless Podman for final-image validation"): (
            "${{ matrix.domain == 'container' || startsWith(matrix.domain, 'e2e-') }}"
        ),
        ("heavy", "Set up the pinned Node runtime for browser and Mermaid tests"): (
            "${{ matrix.domain == 'document-engines' || "
            "startsWith(matrix.domain, 'e2e-') }}"
        ),
        ("heavy", "Retain failed E2E evidence"): (
            "${{ failure() && startsWith(matrix.domain, 'e2e-') }}"
        ),
    }
    for (job_name, step_name), expected_condition in required_conditions.items():
        matches = [
            step
            for step in _job_steps(workflow, job_name)
            if step.get("name") == step_name and step.get("if") == expected_condition
        ]
        if len(matches) != 1:
            errors.append(
                f"missing required workflow condition: {expected_condition!r}"
            )

    evidence_steps = [
        step
        for step in _job_steps(workflow, "heavy")
        if step.get("name") == "Retain failed E2E evidence"
    ]
    evidence_options = (
        _mapping(evidence_steps[0].get("with")) if len(evidence_steps) == 1 else None
    )
    expected_evidence_path = (
        "artifacts/e2e/${{ matrix.domain == 'e2e-standalone' && "
        "'standalone' || 'distributed' }}"
    )
    if (
        evidence_options is None
        or evidence_options.get("path") != expected_evidence_path
    ):
        errors.append(
            f"missing required workflow artifact path: {expected_evidence_path!r}"
        )

    cache_writes = []
    for job_name in jobs:
        for step in _job_steps(workflow, job_name):
            options = _mapping(step.get("with")) or {}
            if "save-cache" in options:
                cache_writes.append(options["save-cache"])
    if cache_writes != [TRUSTED_CACHE_WRITE.removeprefix("save-cache: ")] * 2:
        errors.append("cache writes must be limited to trusted pushes on main")
    return errors


def validate_workflow_text(text: str, *, workflow_name: str = "ci.yml") -> list[str]:
    """Return actionable errors for an allowlisted committed workflow."""
    errors = _validate_lexical_security(text)
    if re.search(r"^\s+[a-z][a-z-]*:\s+write(?:\s|$)", text, re.MULTILINE):
        errors.append("write permission is forbidden in the CI workflow")
    workflow, loading_errors = _load_workflow(text)
    errors.extend(loading_errors)
    policy = READ_ONLY_WORKFLOW_POLICIES.get(workflow_name)
    if policy is None:
        errors.append(f"workflow has no explicit security policy: {workflow_name}")
        return errors
    if workflow is not None:
        errors.extend(_validate_read_only_workflow(workflow, policy=policy))
    if workflow_name == "ci.yml" and workflow is not None:
        errors.extend(_validate_ci_contract(workflow))
    return errors


def _release_steps(job: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    steps = job.get("steps")
    if not isinstance(steps, list):
        return None
    result: list[dict[str, Any]] = []
    for step in steps:
        mapping = _mapping(step)
        if mapping is None:
            return None
        result.append(mapping)
    return result


def _artifact_upload_jobs(
    workflow: Mapping[str, Any], *, artifact_name: object, artifact_path: object
) -> list[str]:
    matches: list[str] = []
    jobs = _mapping(workflow.get("jobs")) or {}
    for job_name, job_value in jobs.items():
        job = _mapping(job_value) or {}
        for step in _release_steps(job) or []:
            uses = step.get("uses")
            options = _mapping(step.get("with")) or {}
            if (
                isinstance(uses, str)
                and uses.startswith("actions/upload-artifact@")
                and options.get("name") == artifact_name
                and str(options.get("path", "")).rstrip("/")
                == str(artifact_path).rstrip("/")
            ):
                matches.append(job_name)
    return matches


def _needed_job_names(value: object) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return set()


def _release_artifact_contract(
    workflow: Mapping[str, Any],
    publish_job: Mapping[str, Any],
    publish_steps: list[dict[str, Any]],
) -> list[str]:
    download = publish_steps[0]
    publish = publish_steps[1]
    download_with = _mapping(download.get("with")) or {}
    publish_with = _mapping(publish.get("with")) or {}
    artifact_name = download_with.get("name")
    download_path = download_with.get("path")
    errors: list[str] = []
    if set(download_with) != {"name", "path"}:
        errors.append("publish download options must be exactly name and path")
    if set(publish_with) != {"packages-dir", "attestations"}:
        errors.append(
            "PyPI publication options must be exactly packages-dir and attestations"
        )
    if not isinstance(artifact_name, str) or not artifact_name:
        errors.append("publish download must name the verified artifact bundle")
    if not isinstance(download_path, str) or not download_path:
        errors.append("publish download must use an explicit artifact path")
    packages_dir = publish_with.get("packages-dir")
    if not isinstance(packages_dir, str) or packages_dir.rstrip("/") != str(
        download_path
    ).rstrip("/"):
        errors.append("PyPI upload must publish only the downloaded artifact directory")
    if publish_with.get("attestations") is not True:
        errors.append("PyPI publication must explicitly generate attestations")
    if "password" in publish_with or "user" in publish_with:
        errors.append(
            "PyPI publication must use Trusted Publishing without credentials"
        )

    upload_jobs = _artifact_upload_jobs(
        workflow, artifact_name=artifact_name, artifact_path=download_path
    )
    if len(upload_jobs) != 1:
        errors.append("exactly one job must upload the verified artifact bundle")
    elif upload_jobs[0] not in _needed_job_names(publish_job.get("needs")):
        errors.append("publish job must depend on the verified artifact uploader")
    return errors


def _validate_release_jobs(
    jobs: Mapping[str, Any], *, publish_job_name: str
) -> list[str]:
    errors: list[str] = []
    for job_name, job_value in jobs.items():
        job = _mapping(job_value)
        if job is None:
            errors.append(f"job {job_name!r} must be a mapping")
            continue
        timeout = job.get("timeout-minutes")
        if (
            not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or not 0 < timeout <= MAX_RELEASE_TIMEOUT_MINUTES
        ):
            errors.append(f"release job {job_name!r} must have a bounded timeout")
        condition = job.get("if")
        if (
            not isinstance(condition, str)
            or condition.strip() != TRUSTED_REPOSITORY_GUARD
        ):
            errors.append(
                f"release job {job_name!r} lacks the trusted repository guard"
            )
        permissions = job.get("permissions")
        if job.get("runs-on") != "ubuntu-24.04":
            errors.append(
                f"release job {job_name!r} must use the allowlisted hosted runner"
            )
        if job_name == publish_job_name:
            if permissions != {"id-token": "write"}:
                errors.append("publish job permissions must be exactly id-token: write")
        elif permissions is not None:
            errors.append(
                f"non-publish release job {job_name!r} must inherit read-only permissions"
            )
    return errors


def _validate_publish_job(
    workflow: Mapping[str, Any], publish_job: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    allowed_job_fields = {
        "environment",
        "if",
        "name",
        "needs",
        "permissions",
        "runs-on",
        "steps",
        "timeout-minutes",
    }
    if set(publish_job).difference(allowed_job_fields):
        errors.append("publish job contains fields outside the minimal allowlist")
    if publish_job.get("environment") != "pypi":
        errors.append("publish job must use the protected pypi environment")
    needs = publish_job.get("needs")
    if not isinstance(needs, (str, list)) or not needs:
        errors.append("publish job must depend on prior artifact validation")
    publish_steps = _release_steps(publish_job)
    if publish_steps is None or len(publish_steps) != PUBLISH_STEP_COUNT:
        errors.append("publish job must contain exactly two action steps")
        return errors
    expected_actions = (
        "actions/download-artifact@",
        "pypa/gh-action-pypi-publish@",
    )
    for step, expected_action in zip(publish_steps, expected_actions, strict=True):
        uses = step.get("uses")
        if set(step).difference({"name", "uses", "with"}):
            errors.append("publish steps may contain only name, uses, and with")
        if not isinstance(uses, str) or not uses.startswith(expected_action):
            errors.append("publish job must only download then publish artifacts")
    errors.extend(_release_artifact_contract(workflow, publish_job, publish_steps))
    return errors


def _validate_release_triggers(
    workflow: Mapping[str, Any], *, approved_triggers: frozenset[str]
) -> list[str]:
    errors: list[str] = []
    if (
        not approved_triggers
        or approved_triggers & RELEASE_FORBIDDEN_TRIGGERS
        or not approved_triggers <= RELEASE_TRIGGER_CANDIDATES
    ):
        errors.append("approved release triggers must contain only trusted contexts")
    triggers = _mapping(workflow.get("on"))
    if triggers is None or set(triggers) != set(approved_triggers):
        errors.append("release triggers do not match the separately approved policy")
        return errors
    release = _mapping(triggers.get("release"))
    if "release" in triggers and (
        release is None or release.get("types") != ["published"]
    ):
        errors.append("release event trigger must be restricted to published releases")
    push = _mapping(triggers.get("push"))
    if "push" in triggers and (
        push is None
        or set(push) != {"tags"}
        or not isinstance(push.get("tags"), list)
        or not push["tags"]
        or not all(
            isinstance(tag, str) and bool(tag.strip()) for tag in push.get("tags", [])
        )
    ):
        errors.append(
            "push release trigger must be restricted to an explicit tag policy"
        )
    return errors


def validate_release_workflow_text(
    text: str,
    *,
    approved_triggers: frozenset[str],
    publish_job_name: str = "publish",
) -> list[str]:
    """Validate a future isolated release workflow under an approved trigger policy."""
    errors = _validate_lexical_security(text)
    workflow, loading_errors = _load_workflow(text)
    errors.extend(loading_errors)
    if workflow is None:
        return errors
    errors.extend(
        _validate_release_triggers(workflow, approved_triggers=approved_triggers)
    )
    if workflow.get("permissions") != READ_ONLY_PERMISSIONS:
        errors.append("release workflow permissions must default to contents: read")
    errors.extend(_validate_concurrency(workflow, expected_prefix="release-"))
    concurrency = _mapping(workflow.get("concurrency")) or {}
    if concurrency.get("cancel-in-progress") is not False:
        errors.append("release publication concurrency must not cancel in progress")

    jobs = _mapping(workflow.get("jobs"))
    if jobs is None or publish_job_name not in jobs:
        errors.append(f"release workflow must define job {publish_job_name!r}")
        return errors
    release_commands = [
        command
        for job_name in jobs
        for step in _job_steps(workflow, job_name)
        if (command := _normalized_command(step.get("run"))) is not None
    ]
    build_invocations = release_commands.count(RELEASE_BUILD_COMMAND)
    unexpected_build_commands = [
        command
        for command in release_commands
        if command != RELEASE_BUILD_COMMAND
        and re.search(r"(?:\buv\s+build\b|\bscripts\.release\.build\b)", command)
    ]
    if build_invocations != 1 or unexpected_build_commands:
        errors.append(
            "release workflow must invoke the distribution build exactly once"
        )

    errors.extend(_validate_release_jobs(jobs, publish_job_name=publish_job_name))
    publish_job = _mapping(jobs[publish_job_name]) or {}
    errors.extend(_validate_publish_job(workflow, publish_job))

    allowed_actions = frozenset(
        {
            "actions/checkout",
            "actions/download-artifact",
            "actions/setup-python",
            "actions/upload-artifact",
            "astral-sh/setup-uv",
            "pypa/gh-action-pypi-publish",
        }
    )
    errors.extend(_validate_action_allowlist(workflow, allowed_actions=allowed_actions))
    errors.extend(_validate_checkout_credentials(workflow))
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


def validate_workflow_files(paths: Iterable[Path]) -> list[str]:
    """Validate every committed workflow against its filename-specific policy."""
    errors: list[str] = []
    for path in sorted(paths):
        if path.name not in READ_ONLY_WORKFLOW_POLICIES:
            errors.append(f"{path}: workflow has no explicit security policy")
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            errors.append(f"{path}: cannot read workflow: {error}")
            continue
        errors.extend(
            f"{path}: {error}"
            for error in validate_workflow_text(text, workflow_name=path.name)
        )
    return errors


def discover_workflow_paths(directory: Path) -> list[Path]:
    """Return every workflow file extension recognized by GitHub Actions."""
    return sorted((*directory.glob("*.yml"), *directory.glob("*.yaml")))


def main() -> int:
    """Validate the committed CI implementation."""
    root = Path(__file__).resolve().parents[2]
    workflow_directory = root / ".github/workflows"
    registry = root / ".github/ci/domains.json"
    workflow_paths = discover_workflow_paths(workflow_directory)
    errors = validate_workflow_files(workflow_paths)
    missing_workflows = set(READ_ONLY_WORKFLOW_POLICIES).difference(
        path.name for path in workflow_paths
    )
    errors.extend(
        f"missing allowlisted workflow: {workflow_name}"
        for workflow_name in sorted(missing_workflows)
    )
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
