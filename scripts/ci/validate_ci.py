"""Perform cheap, repository-local GitHub Actions security checks."""

from __future__ import annotations

import ast
import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from packaging.version import InvalidVersion, Version
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver

from scripts.ci.select_domains import DOMAIN_PATTERNS, load_registry
from scripts.release.artifacts import MANIFEST_NAME as RELEASE_MANIFEST_NAME

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

ACTION_REFERENCE = re.compile(
    r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?)@([0-9a-f]+)$"
)
FULL_SHA = re.compile(r"[0-9a-f]{40}")
SIMPLE_RELEASE_LITERAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
SAFE_RELEASE_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*")
ASCII_CONTROL_LIMIT = 32
ASCII_DELETE = 127
TRUSTED_REPOSITORY = "Guillaume-Lombardo/simple-md-to-docx-converter"
TRUSTED_REPOSITORY_CONDITION = f"github.repository == '{TRUSTED_REPOSITORY}'"
TRUSTED_REPOSITORY_GUARD = f"${{{{ {TRUSTED_REPOSITORY_CONDITION} }}}}"
TRUSTED_CACHE_WRITE = (
    "save-cache: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' "
    "&& github.repository == 'Guillaume-Lombardo/simple-md-to-docx-converter' }}"
)
TRUSTED_LIBREOFFICE_DEB_CACHE_WRITE = (
    "${{ matrix.domain == 'document-engines' && github.event_name == 'push' "
    "&& github.ref == 'refs/heads/main' && github.repository == "
    "'Guillaume-Lombardo/simple-md-to-docx-converter' }}"
)
TRUSTED_LIBREOFFICE_RPM_CACHE_WRITE = (
    "${{ matrix.domain == 'container' && github.event_name == 'push' && "
    "github.ref == 'refs/heads/main' && github.repository == "
    "'Guillaume-Lombardo/simple-md-to-docx-converter' }}"
)
READ_ONLY_PERMISSIONS = {"contents": "read"}
FORBIDDEN_WORKFLOW_KEYS = frozenset({"secrets"})
FORBIDDEN_WORKFLOW_SCALARS = ("--privileged",)
GITHUB_PROPERTY = re.compile(r"\bgithub(?:\.[A-Za-z_][A-Za-z0-9_-]*)+", re.IGNORECASE)
SAFE_GITHUB_PROPERTIES = frozenset(
    {
        "github.event.before",
        "github.event.merge_group.base_sha",
        "github.event.merge_group.head_sha",
        "github.event.pull_request.base.sha",
        "github.event.pull_request.draft",
        "github.event.pull_request.head.sha",
        "github.event.pull_request.head.repo.full_name",
        "github.event.pull_request.number",
        "github.event_name",
        "github.actor",
        "github.head_ref",
        "github.ref",
        "github.repository",
        "github.run_attempt",
        "github.run_id",
        "github.sha",
        "github.token",
    }
)
RELEASE_FORBIDDEN_TRIGGERS = frozenset(
    {"pull_request", "pull_request_target", "merge_group", "workflow_dispatch"}
)
RELEASE_TRIGGER_CANDIDATES = frozenset({"push", "release"})
MAX_RELEASE_TIMEOUT_MINUTES = 60
PUBLISH_STEP_COUNT = 3
PARTIAL_TAG_CHECK_COUNT = 2
RELEASE_CONCURRENCY_GROUP = "release-${{ github.ref }}"
RELEASE_PRODUCER_JOB = "build-and-verify"
PYPI_AVAILABILITY_COMMAND = """\
set -euo pipefail
for url in https://pypi.org/pypi/markweave/json https://pypi.org/simple/markweave/; do
  status="$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "$url")"
  test "$status" = 404
done"""
WORKFLOW_FIELDS = frozenset({"name", "on", "permissions", "concurrency", "jobs"})
ACTION_STEP_FIELDS = frozenset({"name", "uses", "with", "if"})
RUN_STEP_FIELDS = frozenset({"name", "run", "env", "id", "if"})
READ_ONLY_ENV_STEPS = frozenset(
    {
        ("detect", "Select affected domains"),
        ("light", "Enforce changed application line coverage"),
        ("light", "Validate the canonical OpenAPI contract"),
        ("light", "Verify public release alignment"),
        ("domain-plan", "Report runnable and explicitly planned suites"),
        ("heavy", "Prepare verified LibreOffice DEB archive"),
        ("heavy", "Prepare verified LibreOffice RPM archive"),
        ("heavy", "Install verified Pandoc for document-engine tests"),
        (
            "heavy",
            "Install verified fonts and LibreOffice for document-engine tests",
        ),
        ("heavy", "Install verified Mermaid and Chrome for document-engine tests"),
        ("heavy", "Rehearse the exact npm rollback candidate"),
        ("heavy", "Run authenticated conversion workflow in pinned Chrome"),
        ("heavy", "Run selected domain suite without a shell"),
        ("gate", "Require every implemented CI stage"),
        ("mutation", "Run a fresh, non-empty targeted mutation campaign"),
    }
)
READ_ONLY_ID_STEPS = frozenset({("detect", "Select affected domains")})
T67_ROLLBACK_REHEARSAL_CONDITION = (
    "${{ matrix.domain == 'frontend' && github.event_name == 'pull_request' && "
    "github.head_ref == 'chore/T67-pnpm-workspace' && "
    "github.event.pull_request.head.repo.full_name == github.repository }}"
)


@dataclass(frozen=True)
class WorkflowPolicy:
    """Allowlisted shape for one committed read-only workflow."""

    triggers: frozenset[str]
    jobs: Mapping[str, int]
    actions: frozenset[str]
    concurrency_group: str
    cancel_in_progress: bool | str
    job_fields: Mapping[str, frozenset[str]]
    job_conditions: Mapping[str, str]
    step_conditions: Mapping[tuple[str, str], str]
    canonical_digest: str


@dataclass(frozen=True)
class ReleaseWorkflowPolicy:
    """Caller-approved release contract without repository production defaults."""

    approved_triggers: frozenset[str]
    approved_tag_patterns: tuple[str, ...] | None
    distribution_name: str
    version: str
    artifact_upload_action: str
    artifact_download_action: str
    checkout_action: str
    setup_python_action: str
    setup_uv_action: str
    pypi_publish_action: str
    artifact_name: str
    artifact_directory: str
    manifest_name: str
    constraint: str | None
    publishable_paths: tuple[str, ...]


CONTAINER_RELEASE_CANONICAL_DIGEST = (
    "6040a82045b49f57ca16a6e2bf1fd0b109b4e173107b1f117c0260fde4808891"
)
CONTAINER_PAIR_PUBLISHER_CANONICAL_DIGEST = (
    "b180243d6fefbbbe9b4966e50cb5f42dea066cef48d419d0fb034e618758b3fe"
)
RELEASE_IMAGE_ROLES = ("backend", "frontend")
PRODUCTION_RELEASE_CANONICAL_DIGEST = (
    "924bf3cb1e0c45a59942e2f010bdd416ad52636e29358f64ed904462380ee815"
)


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
                "actions/cache/restore",
                "actions/cache/save",
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
        job_fields={
            "detect": frozenset(
                {"name", "outputs", "runs-on", "steps", "timeout-minutes"}
            ),
            "light": frozenset(
                {"name", "permissions", "runs-on", "steps", "timeout-minutes"}
            ),
            "domain-plan": frozenset(
                {"name", "needs", "runs-on", "steps", "timeout-minutes"}
            ),
            "heavy": frozenset(
                {
                    "env",
                    "if",
                    "name",
                    "needs",
                    "runs-on",
                    "services",
                    "steps",
                    "strategy",
                    "timeout-minutes",
                }
            ),
            "gate": frozenset(
                {"if", "name", "needs", "runs-on", "steps", "timeout-minutes"}
            ),
        },
        job_conditions={
            "heavy": "${{ needs.detect.outputs.runnable-domains != '[]' }}",
            "gate": "${{ always() }}",
        },
        step_conditions={
            (
                "light",
                "Enforce changed application line coverage",
            ): (
                "${{ github.event_name == 'pull_request' || "
                "github.event_name == 'merge_group' }}"
            ),
            (
                "light",
                "Save the exact pnpm store cache from trusted main",
            ): (
                "${{ github.event_name == 'push' && github.ref == 'refs/heads/main' "
                "&& github.repository == "
                "'Guillaume-Lombardo/simple-md-to-docx-converter' }}"
            ),
            (
                "heavy",
                "Restore verified LibreOffice DEB archive",
            ): "${{ matrix.domain == 'document-engines' }}",
            (
                "heavy",
                "Prepare verified LibreOffice DEB archive",
            ): "${{ matrix.domain == 'document-engines' }}",
            (
                "heavy",
                "Save verified LibreOffice DEB archive",
            ): TRUSTED_LIBREOFFICE_DEB_CACHE_WRITE,
            (
                "heavy",
                "Restore verified LibreOffice RPM archive",
            ): (
                "${{ matrix.domain == 'container' || "
                "startsWith(matrix.domain, 'e2e-') }}"
            ),
            (
                "heavy",
                "Prepare verified LibreOffice RPM archive",
            ): (
                "${{ matrix.domain == 'container' || "
                "startsWith(matrix.domain, 'e2e-') }}"
            ),
            (
                "heavy",
                "Save verified LibreOffice RPM archive",
            ): TRUSTED_LIBREOFFICE_RPM_CACHE_WRITE,
            (
                "heavy",
                "Install rootless Podman for container validation",
            ): (
                "${{ matrix.domain == 'compose' || matrix.domain == 'container' || "
                "matrix.domain == 'frontend' || startsWith(matrix.domain, 'e2e-') }}"
            ),
            (
                "heavy",
                "Set up pinned Node for frontend smoke",
            ): "${{ matrix.domain == 'frontend' }}",
            (
                "heavy",
                "Install verified Pandoc for document-engine tests",
            ): "${{ matrix.domain == 'document-engines' }}",
            (
                "heavy",
                "Install verified fonts and LibreOffice for document-engine tests",
            ): "${{ matrix.domain == 'document-engines' }}",
            (
                "heavy",
                "Set up the pinned Node runtime for Mermaid tests",
            ): "${{ matrix.domain == 'document-engines' }}",
            (
                "heavy",
                "Set up pinned Node for rootless E2E",
            ): "${{ startsWith(matrix.domain, 'e2e-') }}",
            (
                "heavy",
                "Bootstrap verified Corepack and pnpm for workspace domains",
            ): (
                "${{ matrix.domain == 'frontend' || "
                "startsWith(matrix.domain, 'e2e-') }}"
            ),
            (
                "heavy",
                "Restore the exact pnpm store cache for workspace domains",
            ): (
                "${{ matrix.domain == 'frontend' || "
                "startsWith(matrix.domain, 'e2e-') }}"
            ),
            (
                "heavy",
                "Rehearse the exact npm rollback candidate",
            ): T67_ROLLBACK_REHEARSAL_CONDITION,
            (
                "heavy",
                "Install verified Mermaid and Chrome for document-engine tests",
            ): "${{ matrix.domain == 'document-engines' }}",
            (
                "heavy",
                "Prepare the RustFS test bucket",
            ): "${{ matrix.domain == 'storage-distributed' }}",
            (
                "heavy",
                "Install the locked E2E browser driver",
            ): "${{ startsWith(matrix.domain, 'e2e-') }}",
            (
                "heavy",
                "Retain failed E2E evidence",
            ): "${{ failure() && startsWith(matrix.domain, 'e2e-') }}",
            (
                "heavy",
                "Retain final-image verification evidence",
            ): "${{ always() && matrix.domain == 'container' }}",
        },
        canonical_digest="7d5ba2ebde81201c56e3015b39f95967aa69112aa826ad37296106cd5e97b8b5",
    ),
    "mutation.yml": WorkflowPolicy(
        triggers=frozenset({"schedule", "workflow_dispatch"}),
        jobs={"mutation": 30},
        actions=frozenset(
            {"actions/checkout", "actions/setup-python", "astral-sh/setup-uv"}
        ),
        concurrency_group="mutation-${{ github.ref }}",
        cancel_in_progress=True,
        job_fields={
            "mutation": frozenset({"if", "name", "runs-on", "steps", "timeout-minutes"})
        },
        job_conditions={"mutation": TRUSTED_REPOSITORY_GUARD},
        step_conditions={},
        canonical_digest="506f4cf1a2459e732d987aa0bbe9e5dc43af15df8b922404d750555b346d83a3",
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


def _validate_scalar_security(workflow: object) -> list[str]:
    errors: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(key, str) and key.lower() in FORBIDDEN_WORKFLOW_KEYS:
                    errors.append(f"forbidden workflow key: {key!r}")
                visit(key)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            lowered = value.lower()
            errors.extend(
                f"forbidden workflow scalar: {fragment!r}"
                for fragment in FORBIDDEN_WORKFLOW_SCALARS
                if fragment in lowered
            )
            if re.search(r"\bsecrets\b", value, re.IGNORECASE):
                errors.append("forbidden workflow secret access")
            expression_start = value.find("${{")
            if expression_start >= 0:
                expression_region = value[expression_start + 3 :]
                properties = [
                    property_name.lower()
                    for property_name in GITHUB_PROPERTY.findall(expression_region)
                ]
                unsafe = sorted(set(properties).difference(SAFE_GITHUB_PROPERTIES))
                if unsafe:
                    errors.append(
                        "workflow expression uses non-allowlisted GitHub properties: "
                        f"{unsafe!r}"
                    )
                without_properties = GITHUB_PROPERTY.sub("", expression_region)
                if re.search(r"\bgithub\b", without_properties, re.IGNORECASE):
                    errors.append(
                        "workflow expression must not access the GitHub context dynamically"
                    )

    visit(workflow)
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
    cancellation_mismatch = (
        cancellation is not expected_cancellation
        if isinstance(expected_cancellation, bool)
        else cancellation != expected_cancellation
    )
    if expected_cancellation is not None and cancellation_mismatch:
        errors.append("workflow cancel-in-progress does not match the explicit policy")
    elif not isinstance(cancellation, (bool, str)):
        errors.append("workflow cancel-in-progress must be explicit")
    return errors


def _validate_read_only_step(
    step_value: object, *, job_name: str, policy: WorkflowPolicy
) -> list[str]:
    step = _mapping(step_value)
    if step is None:
        return [f"job {job_name!r} steps must be mappings"]
    errors: list[str] = []
    step_name = step.get("name")
    if not isinstance(step_name, str) or not step_name:
        errors.append(f"job {job_name!r} steps must have explicit names")
    has_uses = "uses" in step
    has_run = "run" in step
    allowed_fields = ACTION_STEP_FIELDS if has_uses else RUN_STEP_FIELDS
    expected_fields = {"name", "uses", "with"} if has_uses else {"name", "run"}
    step_identity = (job_name, str(step_name))
    if step_identity in READ_ONLY_ENV_STEPS:
        expected_fields.add("env")
    if step_identity in READ_ONLY_ID_STEPS:
        expected_fields.add("id")
    expected_condition = policy.step_conditions.get(step_identity)
    if expected_condition is not None:
        expected_fields.add("if")
    if (
        has_uses == has_run
        or set(step).difference(allowed_fields)
        or set(step) != expected_fields
    ):
        errors.append(
            f"step {step_name!r} in job {job_name!r} fields do not match "
            "the exact allowlist"
        )
    if expected_condition is None:
        if "if" in step:
            errors.append(
                f"step {step_name!r} in job {job_name!r} must not define a condition"
            )
    elif step.get("if") != expected_condition:
        errors.append(
            f"step {step_name!r} in job {job_name!r} condition does not match "
            "the explicit policy"
        )
    return errors


def _validate_read_only_job(
    job_name: str, job: Mapping[str, Any], *, policy: WorkflowPolicy
) -> list[str]:
    errors: list[str] = []
    expected_fields = policy.job_fields.get(job_name)
    if expected_fields is None or set(job) != set(expected_fields):
        errors.append(f"job {job_name!r} fields do not match the explicit allowlist")
    expected_condition = policy.job_conditions.get(job_name)
    if expected_condition is None:
        if "if" in job:
            errors.append(f"job {job_name!r} must not define a condition")
    elif job.get("if") != expected_condition:
        errors.append(f"job {job_name!r} condition does not match the explicit policy")
    timeout = job.get("timeout-minutes")
    maximum = policy.jobs.get(job_name)
    if (
        not isinstance(timeout, int)
        or isinstance(timeout, bool)
        or timeout <= 0
        or maximum is None
        or timeout > maximum
    ):
        errors.append(f"job {job_name!r} must define an allowlisted bounded timeout")
    if job_name == "light":
        if job.get("permissions") != {"contents": "read", "packages": "read"}:
            errors.append(
                "light job permissions must be exactly contents: read and packages: read"
            )
    elif "permissions" in job:
        errors.append(
            f"read-only job {job_name!r} must not override workflow permissions"
        )
    if job.get("runs-on") != "ubuntu-24.04":
        errors.append(f"job {job_name!r} must use the allowlisted hosted runner")
    steps = job.get("steps")
    if not isinstance(steps, list):
        return [*errors, f"job {job_name!r} steps must be a list"]
    for step in steps:
        errors.extend(_validate_read_only_step(step, job_name=job_name, policy=policy))
    return errors


def _validate_jobs(workflow: Mapping[str, Any], *, policy: WorkflowPolicy) -> list[str]:
    jobs = _mapping(workflow.get("jobs"))
    if jobs is None:
        return ["workflow jobs must be a mapping"]
    errors: list[str] = []
    if set(jobs) != set(policy.jobs):
        errors.append("workflow jobs do not match the explicit allowlist")
    for job_name, job_value in jobs.items():
        job = _mapping(job_value)
        if job is None:
            errors.append(f"job {job_name!r} must be a mapping")
            continue
        errors.extend(_validate_read_only_job(job_name, job, policy=policy))
    if any("bounded timeout" in error for error in errors):
        errors.append("every job must define a bounded timeout")
    return errors


def _validate_action_allowlist(
    workflow: Mapping[str, Any],
    *,
    allowed_actions: frozenset[str],
    allowed_local_workflows: frozenset[str] = frozenset(),
) -> list[str]:
    errors: list[str] = []
    for reference in _action_references(workflow):
        if not isinstance(reference, str):
            errors.append("every action reference must be a string")
            continue
        if reference in allowed_local_workflows:
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
    canonical = json.dumps(workflow, ensure_ascii=False, separators=(",", ":")).encode()
    actual_digest = hashlib.sha256(canonical).hexdigest()
    if actual_digest != policy.canonical_digest:
        errors.append(
            "workflow values, mapping order, jobs, and steps do not match the reviewed "
            f"canonical policy (actual digest: {actual_digest})"
        )
    if set(workflow) != set(WORKFLOW_FIELDS):
        errors.append("workflow fields do not match the explicit allowlist")
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


def _job_steps(workflow: Mapping[str, Any], job_name: str) -> list[dict[str, Any]]:
    jobs = _mapping(workflow.get("jobs")) or {}
    job = _mapping(jobs.get(job_name)) or {}
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    return [step for value in steps if (step := _mapping(value)) is not None]


def _validate_no_legacy_browser_command(workflow: Mapping[str, Any]) -> list[str]:
    jobs = _mapping(workflow.get("jobs")) or {}
    commands = (
        str((_mapping(step) or {}).get("run", ""))
        for job in jobs.values()
        for step in (_mapping(job) or {}).get("steps", [])
    )
    return (
        ["legacy browser workflow command must not remain in CI"]
        if any("npm run test:web-browser" in command for command in commands)
        else []
    )


def _validate_chrome_downgrade_install(workflow: Mapping[str, Any]) -> list[str]:
    document_engine_install = [
        step.get("run")
        for step in _job_steps(workflow, "heavy")
        if step.get("name")
        == "Install verified Mermaid and Chrome for document-engine tests"
    ]
    return (
        ["pinned Chrome installation must permit an explicit downgrade"]
        if len(document_engine_install) != 1
        or 'apt-get install --yes --allow-downgrades "$chrome_deb"'
        not in str(document_engine_install[0])
        else []
    )


def _validate_public_alignment_credentials(
    workflow: Mapping[str, Any],
) -> list[str]:
    alignment_steps = [
        step
        for step in _job_steps(workflow, "light")
        if step.get("name") == "Verify public release alignment"
    ]
    expected = {
        "BASE_SHA": (
            "${{ github.event.pull_request.base.sha || "
            "github.event.merge_group.base_sha || github.event.before }}"
        ),
        "EVENT_NAME": "${{ github.event_name }}",
        "GHCR_TOKEN": (
            "${{ (github.event_name == 'push' || (github.event_name == "
            "'pull_request' && github.event.pull_request.head.repo.full_name == "
            "github.repository)) && github.token || '' }}"
        ),
        "GHCR_USERNAME": (
            "${{ (github.event_name == 'push' || (github.event_name == "
            "'pull_request' && github.event.pull_request.head.repo.full_name == "
            "github.repository)) && github.actor || '' }}"
        ),
        "HEAD_SHA": (
            "${{ github.event.pull_request.head.sha || "
            "github.event.merge_group.head_sha || github.sha }}"
        ),
    }
    if len(alignment_steps) != 1 or alignment_steps[0].get("env") != expected:
        return [
            "public alignment must receive only the ephemeral read-only GHCR "
            "credentials"
        ]
    return []


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
            "--source-root src/markweave --fail-under 90"
        ),
        ("light", "Validate the canonical OpenAPI contract"): (
            "set -euo pipefail\n"
            "uv run python -m scripts.openapi_contract check\n"
            'if [[ -n "$BASE_SHA" && "$BASE_SHA" != '
            '"0000000000000000000000000000000000000000" ]]; then\n'
            "  uv run python -m scripts.openapi_contract compare "
            '--baseline-git-ref "$BASE_SHA"\n'
            "fi\n"
        ),
        ("light", "Verify public release alignment"): (
            "uv run python -m scripts.release.public_alignment "
            '--event-name "$EVENT_NAME" --base "$BASE_SHA" --head "$HEAD_SHA"'
        ),
        ("light", "Verify the workspace frontend"): (
            'test "$(node --version)" = "v24.19.0"\n'
            'test "$(corepack --version)" = "0.36.0"\n'
            'test "$(pnpm --version)" = "11.25.0"\n'
            "pnpm --filter @markweave/web run check\n"
            "pnpm --filter @markweave/web run build\n"
            "pnpm --filter @markweave/web run test:production\n"
        ),
        ("heavy", "Install the locked E2E browser driver"): (
            "pnpm install --frozen-lockfile --ignore-scripts "
            "--filter md-converter-web-tests"
        ),
        ("heavy", "Rehearse the exact npm rollback candidate"): (
            "bash scripts/javascript/rehearse-npm-rollback.sh "
            '"$T67_CANDIDATE_SHA" "$NPM_BASELINE_SHA"'
        ),
        ("gate", "Require every implemented CI stage"): (
            'set -euo pipefail\n[[ "$DETECT_RESULT" == "success" ]]\n'
            '[[ "$DOMAIN_PLAN_RESULT" == "success" ]]\n'
            'if [[ "$RUNNABLE_DOMAINS" == "[]" ]]; then\n'
            '  [[ "$HEAVY_RESULT" == "skipped" ]]\nelse\n'
            '  [[ "$HEAVY_RESULT" == "success" ]]\nfi\n'
            '[[ "$LIGHT_RESULT" == "success" ]]\n'
        ),
    }
    for (job_name, step_name), expected_command in required_commands.items():
        matches = [
            step
            for step in _job_steps(workflow, job_name)
            if step.get("name") == step_name
        ]
        if len(matches) != 1 or matches[0].get("run") != expected_command:
            errors.append(f"missing required workflow command: {expected_command!r}")

    errors.extend(_validate_no_legacy_browser_command(workflow))
    errors.extend(_validate_chrome_downgrade_install(workflow))
    errors.extend(_validate_public_alignment_credentials(workflow))

    required_conditions = {
        ("light", "Enforce changed application line coverage"): (
            "${{ github.event_name == 'pull_request' || "
            "github.event_name == 'merge_group' }}"
        ),
        ("heavy", "Install rootless Podman for container validation"): (
            "${{ matrix.domain == 'compose' || matrix.domain == 'container' || "
            "matrix.domain == 'frontend' || startsWith(matrix.domain, 'e2e-') }}"
        ),
        ("heavy", "Set up pinned Node for frontend smoke"): (
            "${{ matrix.domain == 'frontend' }}"
        ),
        ("heavy", "Set up the pinned Node runtime for Mermaid tests"): (
            "${{ matrix.domain == 'document-engines' }}"
        ),
        ("heavy", "Set up pinned Node for rootless E2E"): (
            "${{ startsWith(matrix.domain, 'e2e-') }}"
        ),
        ("heavy", "Rehearse the exact npm rollback candidate"): (
            T67_ROLLBACK_REHEARSAL_CONDITION
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

    frontend_node_steps = [
        step
        for step in _job_steps(workflow, "heavy")
        if step.get("name") == "Set up pinned Node for frontend smoke"
    ]
    frontend_node_options = (
        _mapping(frontend_node_steps[0].get("with"))
        if len(frontend_node_steps) == 1
        else None
    )
    if (
        len(frontend_node_steps) != 1
        or frontend_node_steps[0].get("uses")
        != "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020"
        or frontend_node_options
        != {
            "node-version": "24.19.0",
            "check-latest": False,
        }
    ):
        errors.append("frontend smoke must use the reviewed pinned Node setup")

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
    libreoffice_cache_writes = [
        step.get("if")
        for job_name in jobs
        for step in _job_steps(workflow, job_name)
        if str(step.get("name", "")).startswith("Save verified LibreOffice")
        and isinstance(step.get("uses"), str)
        and step["uses"].startswith("actions/cache/save@")
    ]
    if libreoffice_cache_writes != [
        TRUSTED_LIBREOFFICE_DEB_CACHE_WRITE,
        TRUSTED_LIBREOFFICE_RPM_CACHE_WRITE,
    ]:
        errors.append(
            "LibreOffice cache writes must be limited to trusted main domains"
        )
    return errors


def validate_workflow_text(text: str, *, workflow_name: str = "ci.yml") -> list[str]:
    """Return actionable errors for an allowlisted committed workflow."""
    errors: list[str] = []
    if re.search(r"^\s+[a-z][a-z-]*:\s+write(?:\s|$)", text, re.MULTILINE):
        errors.append("write permission is forbidden in the CI workflow")
    workflow, loading_errors = _load_workflow(text)
    errors.extend(loading_errors)
    policy = READ_ONLY_WORKFLOW_POLICIES.get(workflow_name)
    if policy is None:
        errors.append(f"workflow has no explicit security policy: {workflow_name}")
        return errors
    if workflow is not None:
        errors.extend(_validate_scalar_security(workflow))
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


def _artifact_upload_steps(
    workflow: Mapping[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    matches: list[tuple[str, dict[str, Any]]] = []
    jobs = _mapping(workflow.get("jobs")) or {}
    for job_name, job_value in jobs.items():
        job = _mapping(job_value) or {}
        for step in _release_steps(job) or []:
            uses = step.get("uses")
            if isinstance(uses, str) and uses.startswith("actions/upload-artifact@"):
                matches.append((job_name, step))
    return matches


def _release_artifact_contract(
    workflow: Mapping[str, Any],
    publish_job: Mapping[str, Any],
    publish_steps: list[dict[str, Any]],
    *,
    policy: ReleaseWorkflowPolicy,
) -> list[str]:
    download = publish_steps[1]
    publish = publish_steps[2]
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
    if (
        artifact_name != policy.artifact_name
        or download_path != policy.artifact_directory
    ):
        errors.append(
            "publish download must match the caller-approved artifact contract"
        )
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

    uploads = _artifact_upload_steps(workflow)
    if len(uploads) != 1:
        errors.append("exactly one job must upload the verified artifact bundle")
    else:
        upload_job, upload_step = uploads[0]
        upload_with = _mapping(upload_step.get("with")) or {}
        if (
            upload_job != RELEASE_PRODUCER_JOB
            or set(upload_step) != {"name", "uses", "with"}
            or set(upload_with) != {"if-no-files-found", "name", "path"}
            or upload_with.get("name") != artifact_name
            or upload_with.get("path") != "\n".join(policy.publishable_paths) + "\n"
            or upload_with.get("if-no-files-found") != "error"
        ):
            errors.append(
                "verified artifact upload must be unique, unconditional, and exact"
            )
        if publish_job.get("needs") != upload_job:
            errors.append(
                "publish job must depend only on the verified artifact uploader"
            )
    return errors


def _release_commands(policy: ReleaseWorkflowPolicy) -> tuple[str, str, str]:
    build_argv = [
        "uv",
        "run",
        "python",
        "-m",
        "scripts.release.build",
        "--output",
        policy.artifact_directory,
        "--name",
        policy.distribution_name,
        "--version",
        policy.version,
    ]
    if policy.constraint is not None:
        build_argv.extend(("--constraint", policy.constraint))
    shared_argv = [
        "--directory",
        policy.artifact_directory,
        "--name",
        policy.distribution_name,
        "--version",
        policy.version,
        "--manifest-name",
        policy.manifest_name,
    ]
    verify_argv = [
        "uv",
        "run",
        "python",
        "-m",
        "scripts.release.artifacts",
        "verify",
        *shared_argv,
    ]
    install_argv = [
        "uv",
        "run",
        "python",
        "-m",
        "scripts.release.verify_install",
        *shared_argv,
    ]
    return shlex.join(build_argv), shlex.join(verify_argv), shlex.join(install_argv)


def _release_identity_command(version: str) -> str:
    return (
        'set -euo pipefail\ntest "$GITHUB_REF_TYPE" = tag\n'
        f'test "$GITHUB_REF_NAME" = v{shlex.quote(version)}'
    )


def _validate_release_producer_job(
    job: Mapping[str, Any], *, policy: ReleaseWorkflowPolicy
) -> list[str]:
    errors: list[str] = []
    expected_fields = {"if", "runs-on", "steps", "timeout-minutes"}
    if set(job) != expected_fields:
        errors.append("release producer job fields do not match the exact contract")
    steps = _release_steps(job)
    if steps is None:
        return [*errors, "release producer steps must be mappings"]
    build_command, verify_command, install_command = _release_commands(policy)
    expected_steps: tuple[tuple[str, str, object], ...] = (
        (
            "Check out reviewed source",
            "uses",
            {"persist-credentials": False},
        ),
        (
            "Set up clean Python 3.14",
            "uses",
            {"python-version": "3.14", "check-latest": False},
        ),
        (
            "Set up uv",
            "uses",
            {"version": "0.12.1", "enable-cache": False},
        ),
        (
            "Validate the reviewed release identity",
            "run",
            _release_identity_command(policy.version) + "\n",
        ),
        ("Build distributions exactly once", "run", build_command),
        (
            "Verify artifact integrity and metadata",
            "run",
            verify_command,
        ),
        (
            "Verify clean Python 3.14 installation and public import",
            "run",
            install_command,
        ),
        (
            "Transfer verified artifacts",
            "uses",
            {
                "name": policy.artifact_name,
                "path": "\n".join(policy.publishable_paths) + "\n",
                "if-no-files-found": "error",
            },
        ),
    )
    if len(steps) != len(expected_steps):
        return [*errors, "release producer steps do not match the exact contract"]
    expected_actions = (
        policy.checkout_action,
        policy.setup_python_action,
        policy.setup_uv_action,
        None,
        None,
        None,
        None,
        policy.artifact_upload_action,
    )
    for step, (name, kind, payload), action in zip(
        steps, expected_steps, expected_actions, strict=True
    ):
        expected_step_fields = {"name", kind}
        if payload is not None and kind == "uses":
            expected_step_fields.add("with")
        if set(step) != expected_step_fields or step.get("name") != name:
            errors.append("release producer steps do not match the exact contract")
            continue
        if kind == "run":
            if step.get("run") != payload:
                errors.append(
                    "release producer commands do not match the exact contract"
                )
        else:
            uses = step.get("uses")
            action_matches = uses == action
            if not action_matches:
                errors.append(
                    "release producer actions do not match the exact contract"
                )
            if payload is not None and step.get("with") != payload:
                errors.append(
                    "release producer action options do not match the exact contract"
                )
    python_options = _mapping(steps[1].get("with")) or {}
    if python_options.get("check-latest") is not False:
        errors.append("release Python setup check-latest must be boolean false")
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
        expected_fields = (
            {
                "environment",
                "if",
                "needs",
                "permissions",
                "runs-on",
                "steps",
                "timeout-minutes",
            }
            if job_name == publish_job_name
            else {"if", "runs-on", "steps", "timeout-minutes"}
        )
        if set(job) != expected_fields:
            errors.append(
                f"release job {job_name!r} fields do not match the exact contract"
            )
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
    workflow: Mapping[str, Any],
    publish_job: Mapping[str, Any],
    *,
    policy: ReleaseWorkflowPolicy,
) -> list[str]:
    errors: list[str] = []
    expected_job_fields = {
        "environment",
        "if",
        "needs",
        "permissions",
        "runs-on",
        "steps",
        "timeout-minutes",
    }
    if set(publish_job) != expected_job_fields:
        errors.append("publish job fields do not match the exact minimal contract")
    if publish_job.get("environment") != "pypi":
        errors.append("publish job must use the dedicated pypi environment identity")
    if publish_job.get("needs") != RELEASE_PRODUCER_JOB:
        errors.append("publish job must depend only on prior artifact validation")
    publish_steps = _release_steps(publish_job)
    if publish_steps is None or len(publish_steps) != PUBLISH_STEP_COUNT:
        errors.append("publish job must contain exactly three approved steps")
        return errors
    preflight = publish_steps[0]
    if preflight != {
        "name": "Recheck PyPI name availability",
        "run": PYPI_AVAILABILITY_COMMAND + "\n",
    }:
        errors.append("publish job must fail closed on first-release name availability")
    expected_actions = (
        policy.artifact_download_action,
        policy.pypi_publish_action,
    )
    for step, expected_action in zip(publish_steps[1:], expected_actions, strict=True):
        uses = step.get("uses")
        if set(step) != {"name", "uses", "with"}:
            errors.append("publish steps must contain exactly name, uses, and with")
        if uses != expected_action:
            errors.append("publish job must only download then publish artifacts")
    errors.extend(
        _release_artifact_contract(workflow, publish_job, publish_steps, policy=policy)
    )
    return errors


def _validate_release_triggers(
    workflow: Mapping[str, Any],
    *,
    policy: ReleaseWorkflowPolicy,
) -> list[str]:
    errors: list[str] = []
    approved_triggers = (
        policy.approved_triggers
        if isinstance(policy.approved_triggers, frozenset)
        and all(isinstance(trigger, str) for trigger in policy.approved_triggers)
        else frozenset()
    )
    if approved_triggers != policy.approved_triggers:
        errors.append("approved release triggers must be a frozenset of strings")
    approved_tag_patterns = (
        policy.approved_tag_patterns
        if policy.approved_tag_patterns is None
        or isinstance(policy.approved_tag_patterns, tuple)
        else None
    )
    if approved_tag_patterns is not policy.approved_tag_patterns:
        errors.append("approved release tag patterns must be an explicit tuple or None")
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
        release is None
        or set(release) != {"types"}
        or release.get("types") != ["published"]
    ):
        errors.append("release event trigger must be restricted to published releases")
    push = _mapping(triggers.get("push"))
    if "push" in triggers and (
        approved_tag_patterns is None
        or not approved_tag_patterns
        or not all(_is_github_tag_pattern(tag) for tag in approved_tag_patterns)
        or push is None
        or set(push) != {"tags"}
        or not isinstance(push.get("tags"), list)
        or not push["tags"]
        or not all(_is_github_tag_pattern(tag) for tag in push.get("tags", []))
    ):
        errors.append(
            "push release trigger must match explicitly approved tag patterns"
        )
    elif (
        "push" in triggers
        and push is not None
        and push.get("tags") != list(approved_tag_patterns or ())
    ):
        errors.append("push release tag patterns do not match the approved policy")
    if "push" not in triggers and approved_tag_patterns is not None:
        errors.append("tag patterns may be approved only for a push release trigger")
    return errors


def _is_simple_release_literal(value: object) -> bool:
    return (
        isinstance(value, str) and SIMPLE_RELEASE_LITERAL.fullmatch(value) is not None
    )


def _is_github_tag_pattern(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and "${{" not in value
        and not any(
            ord(character) < ASCII_CONTROL_LIMIT or ord(character) == ASCII_DELETE
            for character in value
        )
    )


def _is_distribution_name(value: object) -> bool:
    return _is_simple_release_literal(value)


def _is_pep440_version(value: object) -> bool:
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        Version(value)
    except InvalidVersion:
        return False
    return True


def _is_artifact_name(value: object) -> bool:
    return _is_simple_release_literal(value)


def _is_manifest_name(value: object) -> bool:
    return _is_simple_release_literal(value)


def _is_safe_release_path(value: object) -> bool:
    if not isinstance(value, str) or SAFE_RELEASE_PATH.fullmatch(value) is None:
        return False
    return not value.startswith("/") and ".." not in Path(value).parts


def _is_artifact_directory(value: object) -> bool:
    return _is_safe_release_path(value)


def _is_constraint(value: object) -> bool:
    return value is None or _is_safe_release_path(value)


def _validate_release_action(reference: object, *, expected_action: str) -> bool:
    if not isinstance(reference, str):
        return False
    match = ACTION_REFERENCE.fullmatch(reference)
    return (
        match is not None
        and match.group(1) == expected_action
        and FULL_SHA.fullmatch(match.group(2)) is not None
    )


def _validate_release_policy(policy: ReleaseWorkflowPolicy) -> list[str]:
    errors: list[str] = []
    if not _is_distribution_name(policy.distribution_name):
        errors.append("caller-approved distribution name must be a simple literal")
    if not _is_pep440_version(policy.version):
        errors.append("caller-approved version must be a valid PEP 440 version")
    if not _is_artifact_name(policy.artifact_name):
        errors.append("caller-approved artifact name must be a simple literal")
    if not _is_manifest_name(policy.manifest_name):
        errors.append("caller-approved manifest name must be a simple literal")
    if not _is_artifact_directory(policy.artifact_directory):
        errors.append(
            "caller-approved artifact directory must be a safe relative literal path"
        )
    if not _is_constraint(policy.constraint):
        errors.append("caller-approved constraint must be a safe relative literal path")
    if not isinstance(policy.publishable_paths, tuple) or set(
        policy.publishable_paths
    ) != {
        f"{policy.artifact_directory}/*.whl",
        f"{policy.artifact_directory}/*.tar.gz",
    }:
        errors.append(
            "caller-approved publishable paths must select only wheel and sdist files"
        )
    if policy.manifest_name != RELEASE_MANIFEST_NAME:
        errors.append(
            "caller-approved manifest name must match the release build default"
        )
    action_fields = {
        "checkout": (policy.checkout_action, "actions/checkout"),
        "setup-python": (policy.setup_python_action, "actions/setup-python"),
        "setup-uv": (policy.setup_uv_action, "astral-sh/setup-uv"),
        "upload-artifact": (
            policy.artifact_upload_action,
            "actions/upload-artifact",
        ),
        "download-artifact": (
            policy.artifact_download_action,
            "actions/download-artifact",
        ),
        "PyPI publish": (
            policy.pypi_publish_action,
            "pypa/gh-action-pypi-publish",
        ),
    }
    errors.extend(
        f"caller-approved {label} action must be an immutable exact action"
        for label, (reference, expected) in action_fields.items()
        if not _validate_release_action(reference, expected_action=expected)
    )
    return errors


def validate_release_workflow_text(
    text: str,
    *,
    policy: ReleaseWorkflowPolicy,
) -> list[str]:
    """Validate a future isolated release workflow under an approved trigger policy."""
    errors = _validate_release_policy(policy)
    publish_job_name = "publish"
    workflow, loading_errors = _load_workflow(text)
    errors.extend(loading_errors)
    if workflow is None:
        return errors
    errors.extend(_validate_scalar_security(workflow))
    errors.extend(_validate_release_triggers(workflow, policy=policy))
    if set(workflow) != set(WORKFLOW_FIELDS):
        errors.append("release workflow fields do not match the exact allowlist")
    if workflow.get("permissions") != READ_ONLY_PERMISSIONS:
        errors.append("release workflow permissions must default to contents: read")
    errors.extend(
        _validate_concurrency(
            workflow,
            expected_group=RELEASE_CONCURRENCY_GROUP,
            expected_cancellation=False,
        )
    )
    concurrency = _mapping(workflow.get("concurrency")) or {}
    if concurrency.get("cancel-in-progress") is not False:
        errors.append("release publication concurrency must not cancel in progress")

    jobs = _mapping(workflow.get("jobs"))
    if jobs is None or publish_job_name not in jobs:
        errors.append(f"release workflow must define job {publish_job_name!r}")
        return errors
    if set(jobs) != {RELEASE_PRODUCER_JOB, publish_job_name}:
        errors.append("release jobs do not match the exact build and publish contract")
    errors.extend(_validate_release_jobs(jobs, publish_job_name=publish_job_name))
    producer_job = _mapping(jobs.get(RELEASE_PRODUCER_JOB)) or {}
    errors.extend(_validate_release_producer_job(producer_job, policy=policy))
    publish_job = _mapping(jobs[publish_job_name]) or {}
    errors.extend(_validate_publish_job(workflow, publish_job, policy=policy))

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


def validate_container_publish_pair_text(text: str) -> list[str]:
    """Validate the exact-byte, preflight-before-copy pair publisher."""
    errors: list[str] = []
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != (
        CONTAINER_PAIR_PUBLISHER_CANONICAL_DIGEST
    ):
        errors.append("container pair publisher differs from the reviewed policy")
    required_fragments = (
        "set -euo pipefail",
        "for role in backend frontend; do",
        "scripts.container.verify_supply_chain verify",
        '"oci-archive:$artifacts/$role/image.oci.tar" "dir:$staging_root/$role"',
        "skopeo copy --preserve-digests",
        'test "sha256:$(sha256sum "$staging_root/$role/manifest.json"',
        "ghcr.io/guillaume-lombardo/md-converter-web",
        'skopeo copy --authfile "$registry_auth_file" --preserve-digests --retry-times 3',
        'skopeo login --authfile "$registry_auth_file"',
        "--password-stdin ghcr.io",
        'skopeo copy --authfile "$registry_auth_file"',
        'chmod 0600 "$registry_auth_file"',
        '[[ "$copied_digest" = "${intended_digests[$role]}" ]]',
        'if remote_digest="$(inspect_remote_tag "$role" "$tag")"; then',
        'test "$remote_digest" = "${intended_digests[$role]}"',
        'test "$(inspect_remote_tag "$role" "$tag")" = "${intended_digests[$role]}"',
        "scripts.container.release_pair create",
        "backend-digest=%s\\nfrontend-digest=%s\\n",
    )
    errors.extend(
        f"container pair publisher is missing: {required}"
        for required in required_fragments
        if required not in text
    )
    if "set +e" in text or "--privileged" in text.casefold():
        errors.append("container pair publisher weakens the execution boundary")
    stage_loop = text.find("for role in backend frontend; do")
    copy_loop = text.find("for role in backend frontend; do", stage_loop + 1)
    stage = text.find("oci-archive:$artifacts/$role/image.oci.tar", stage_loop)
    preflight = text.find('inspect_remote_tag "$role" "$tag"', stage)
    copy = text.find('copy_staged_tag "$role" "$tag"', copy_loop)
    if not 0 <= stage_loop < stage < preflight < copy_loop < copy:
        errors.append(
            "container pair publisher must preflight both images before any copy"
        )
    return errors


def validate_container_release_workflow_text(  # noqa: PLR0912, PLR0915
    text: str,
) -> list[str]:
    """Validate the exact least-privilege container publication contract."""
    workflow, errors = _load_workflow(text)
    if workflow is None:
        return errors
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != (
        CONTAINER_RELEASE_CANONICAL_DIGEST
    ):
        errors.append("container release workflow differs from the reviewed policy")
    triggers = _mapping(workflow.get("on"))
    expected_inputs = {
        "artifact-run-id": {"required": False, "type": "string"},
        "version": {"required": True, "type": "string"},
        "tag": {"required": True, "type": "string"},
        "source-sha": {"required": True, "type": "string"},
    }
    expected_recovery_inputs = {
        "artifact-run-id": {
            "description": "Successful build run retaining the exact container artifact",
            "required": True,
            "type": "string",
        },
        "version": expected_inputs["version"],
        "tag": expected_inputs["tag"],
        "source-sha": expected_inputs["source-sha"],
    }
    if triggers != {
        "workflow_call": {"inputs": expected_inputs},
        "workflow_dispatch": {"inputs": expected_recovery_inputs},
    }:
        errors.append(
            "container publication must be an exact secretless reusable and recovery workflow"
        )
    if workflow.get("permissions") != READ_ONLY_PERMISSIONS:
        errors.append("container release permissions must default to contents: read")
    if set(workflow) != {"name", "on", "permissions", "jobs"}:
        errors.append("container release workflow fields do not match the allowlist")
    jobs = _mapping(workflow.get("jobs")) or {}
    if set(jobs) != {
        "build-and-publish",
        "recover-evidence",
        "attest",
        "release-evidence",
    }:
        errors.append("container release jobs do not match the exact contract")
    expected_permissions = {
        "build-and-publish": {"contents": "read", "packages": "write"},
        "recover-evidence": {
            "actions": "read",
            "contents": "read",
            "packages": "write",
        },
        "attest": {
            "attestations": "write",
            "contents": "read",
            "id-token": "write",
            "packages": "write",
        },
        "release-evidence": {"contents": "write"},
    }
    for name, expected in expected_permissions.items():
        job = _mapping(jobs.get(name)) or {}
        if job.get("permissions") != expected:
            errors.append(f"container release job {name!r} permissions are not minimal")
        condition = job.get("if")
        required_conditions = {
            "build-and-publish": (
                TRUSTED_REPOSITORY_CONDITION,
                "github.event_name == 'push'",
                "github.ref == 'refs/heads/main'",
                "github.sha == inputs.source-sha",
            ),
            "recover-evidence": (
                TRUSTED_REPOSITORY_CONDITION,
                "github.event_name == 'workflow_dispatch'",
                "github.ref == 'refs/heads/main'",
            ),
            "attest": (
                "always()",
                TRUSTED_REPOSITORY_CONDITION,
                "github.event_name == 'push'",
                "github.event_name == 'workflow_dispatch'",
                "needs.build-and-publish.result == 'success'",
                "needs.recover-evidence.result == 'success'",
            ),
            "release-evidence": (
                "always()",
                TRUSTED_REPOSITORY_CONDITION,
                "github.event_name == 'push'",
                "github.event_name == 'workflow_dispatch'",
                "needs.build-and-publish.result == 'success'",
                "needs.recover-evidence.result == 'success'",
                "needs.attest.result == 'success'",
            ),
        }[name]
        if not isinstance(condition, str) or not all(
            clause in condition for clause in required_conditions
        ):
            errors.append(f"container release job {name!r} lacks the repository guard")
        if (
            name == "build-and-publish"
            and isinstance(condition, str)
            and "workflow_dispatch" in condition
        ):
            errors.append("manual container recovery must not enter the build job")
        if (
            name == "recover-evidence"
            and isinstance(condition, str)
            and "github.event_name == 'push'" in condition
        ):
            errors.append("automatic publication must not enter the recovery job")
        timeout = job.get("timeout-minutes")
        maximum_timeout = (
            120 if name == "build-and-publish" else MAX_RELEASE_TIMEOUT_MINUTES
        )
        if (
            not isinstance(timeout, int)
            or isinstance(timeout, bool)
            or not 0 < timeout <= maximum_timeout
        ):
            errors.append(f"container release job {name!r} lacks a bounded timeout")
    errors.extend(
        _validate_action_allowlist(
            workflow,
            allowed_actions=frozenset(
                {
                    "actions/attest-build-provenance",
                    "actions/checkout",
                    "actions/download-artifact",
                    "actions/setup-node",
                    "actions/setup-python",
                    "actions/upload-artifact",
                    "astral-sh/setup-uv",
                    "docker/login-action",
                }
            ),
        )
    )
    errors.extend(_validate_checkout_credentials(workflow))
    if re.search(r"\bsecrets\b", text, re.IGNORECASE):
        errors.append("container release workflow must not access stored secrets")
    if "--privileged" in text.casefold():
        errors.append("container release workflow must not use privileged containers")
    try:
        pair_publisher = Path("scripts/container/publish-release-pair.sh").read_text(
            encoding="utf-8"
        )
    except OSError:
        errors.append("container release pair publisher is missing")
        pair_publisher = ""
    errors.extend(validate_container_publish_pair_text(pair_publisher))
    for required in (
        'test "$RELEASE_TAG" = "v$RELEASE_VERSION"',
        'test "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$RELEASE_TAG" --jq .object.sha)" = "$SOURCE_SHA"',
        "[.tag_name, .target_commitish, .draft, .prerelease] | @tsv",
        '"localhost/md-converter:$RELEASE_VERSION"',
        'bash scripts/container/recovery-cli-smoke.sh "$backend_image"',
        '"localhost/md-converter-web:$RELEASE_VERSION"',
        'MARKWEAVE_E2E_LOCAL_IMAGE="localhost/md-converter:$RELEASE_VERSION"',
        'MARKWEAVE_E2E_LOCAL_FRONTEND_IMAGE="localhost/md-converter-web:$RELEASE_VERSION"',
        "bash scripts/e2e/run.sh standalone",
        "bash scripts/e2e/run.sh distributed",
        "container-release-${{ inputs.tag }}",
        "sudo apt-get install --yes podman skopeo",
        "skopeo --version",
        "scripts/container/publish-release-pair.sh",
        "scripts.container.release_pair verify",
        "artifact-ids: ${{ steps.identity.outputs.artifact-id }}",
        "run-id: ${{ inputs.artifact-run-id }}",
        "merge-multiple: true",
        ".head_repository.id == $repository_id",
        '.path == ".github/workflows/container-release.yml"',
        '.event == "workflow_dispatch"',
        '.conclusion == "failure"',
        'git merge-base --is-ancestor "$SOURCE_SHA" "$run_sha"',
        'git merge-base --is-ancestor "$run_sha" "$GITHUB_SHA"',
        '.name == "build-and-publish"',
        '.conclusion == "success"',
        "container-stage-$RELEASE_TAG",
        ".size_in_bytes <= 4000000000",
        ".expired == false",
        "Retain staged pair before registry mutation",
        "container-stage-${{ inputs.tag }}",
        "needs.build-and-publish.outputs.backend-digest || needs.recover-evidence.outputs.backend-digest",
        "needs.build-and-publish.outputs.frontend-digest || needs.recover-evidence.outputs.frontend-digest",
        '--repo "$GITHUB_REPOSITORY"',
        "--clobber",
    ):
        if required not in text:
            errors.append(f"container release is missing dynamic contract: {required}")
    identity_steps = [
        step
        for step in _job_steps(workflow, "build-and-publish")
        if step.get("name") == "Validate the reviewed release identity"
    ]
    identity_run = identity_steps[0].get("run") if len(identity_steps) == 1 else None
    for required in (
        'test "$(gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$RELEASE_TAG" --jq .object.sha)" = "$SOURCE_SHA"',
        "[.tag_name, .target_commitish, .draft, .prerelease] | @tsv",
        "= \"$RELEASE_TAG\"$'\\t'\"$SOURCE_SHA\"$'\\tfalse\\tfalse'",
    ):
        if not isinstance(identity_run, str) or required not in identity_run:
            errors.append(
                f"container recovery identity validation is missing: {required}"
            )
    build_steps = [
        step
        for step in _job_steps(workflow, "build-and-publish")
        if step.get("name") == "Build and validate the final rootless image pair once"
    ]
    build_run = build_steps[0].get("run") if len(build_steps) == 1 else None
    for required in (
        'SOURCE_DATE_EPOCH="$source_date_epoch" bash scripts/container/build.sh "$backend_image"',
        'podman build --format oci --timestamp "$source_date_epoch"',
        'bash scripts/container/recovery-cli-smoke.sh "$backend_image"',
        'bash web/scripts/run-rootless-smoke.sh "$frontend_image" --existing',
    ):
        if not isinstance(build_run, str) or required not in build_run:
            errors.append(f"automatic container build is missing: {required}")
    if isinstance(build_run, str) and "workflow_dispatch" in build_run:
        errors.append("manual container recovery must not rebuild the image")
    recovery_steps = _job_steps(workflow, "recover-evidence")
    recovery_names = [step.get("name") for step in recovery_steps]
    expected_recovery_names = [
        "Check out the trusted recovery implementation",
        "Set up Python 3.14",
        "Set up uv",
        "Synchronize locked recovery dependencies",
        "Validate release, source run, artifact, and public image identity",
        "Install rootless Podman and Skopeo for exact-byte recovery",
        "Download the exact retained artifact by immutable ID",
        "Recover or verify the exact retained pair publication",
        "Verify retained bundle integrity and release identity",
        "Transfer the exact verified evidence to this recovery run",
    ]
    if recovery_names != expected_recovery_names:
        errors.append("container recovery steps do not match the exact artifact flow")
    recovery_identity = next(
        (
            step.get("run")
            for step in recovery_steps
            if step.get("name")
            == "Validate release, source run, artifact, and public image identity"
        ),
        None,
    )
    for required in (
        'test "$RELEASE_TAG" = "v$RELEASE_VERSION"',
        'git show "$SOURCE_SHA:pyproject.toml"',
        'actions/workflows/container-release.yml" --jq .id',
        ".repository.id == $repository_id",
        ".head_repository.id == $repository_id",
        ".workflow_run.repository_id == $repository_id",
        ".workflow_run.head_repository_id == $repository_id",
        "container-stage-$RELEASE_TAG",
        'git merge-base --is-ancestor "$SOURCE_SHA" "$run_sha"',
        'git merge-base --is-ancestor "$run_sha" "$GITHUB_SHA"',
        "printf 'artifact-id=%s\\n'",
    ):
        if not isinstance(recovery_identity, str) or required not in recovery_identity:
            errors.append(
                f"container retained-artifact recovery is missing: {required}"
            )
    attest_steps = _job_steps(workflow, "attest")
    attest_login = [
        index
        for index, step in enumerate(attest_steps)
        if step.get("name") == "Log in to GHCR for attestation publication"
        and step.get("uses", "").startswith("docker/login-action@")
        and step.get("with")
        == {
            "registry": "ghcr.io",
            "username": "${{ github.actor }}",
            "password": "${{ github.token }}",
        }
    ]
    provenance = [
        index
        for index, step in enumerate(attest_steps)
        if step.get("name")
        in {
            "Attest the published backend image identity",
            "Attest the published frontend image identity",
        }
        and step.get("uses", "").startswith("actions/attest-build-provenance@")
    ]
    if (
        len(attest_login) != 1
        or len(provenance) != len(RELEASE_IMAGE_ROLES)
        or any(attest_login[0] >= index for index in provenance)
    ):
        errors.append("container attestation must authenticate to GHCR before push")
    evidence_steps = _job_steps(workflow, "release-evidence")
    evidence_run = next(
        (
            step.get("run")
            for step in evidence_steps
            if step.get("name")
            == "Verify the exact published Release and attach evidence idempotently"
        ),
        None,
    )
    if (
        not isinstance(evidence_run, str)
        or 'gh release upload "$RELEASE_TAG"' not in evidence_run
        or '--repo "$GITHUB_REPOSITORY"' not in evidence_run
    ):
        errors.append(
            "container Release evidence upload must bind the explicit repository"
        )
    publish_steps = [
        step
        for step in _job_steps(workflow, "build-and-publish")
        if step.get("name")
        == "Publish the bound pair without overwriting a conflicting image"
    ]
    publish_run = publish_steps[0].get("run") if len(publish_steps) == 1 else None
    if not isinstance(publish_run, str):
        errors.append("container release lacks the unique guarded publication step")
    elif "scripts/container/publish-release-pair.sh" not in publish_run:
        errors.append(
            "container release must invoke the reviewed pair publication boundary"
        )
    return errors


def validate_production_release_workflow_text(  # noqa: PLR0912, PLR0915
    text: str,
) -> list[str]:
    """Validate the exact trusted-main automatic publication orchestrator."""
    workflow, errors = _load_workflow(text)
    if workflow is None:
        return errors
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != (
        PRODUCTION_RELEASE_CANONICAL_DIGEST
    ):
        errors.append("automatic release workflow differs from the reviewed policy")
    triggers = _mapping(workflow.get("on"))
    if triggers != {"push": {"branches": ["main"], "paths": ["pyproject.toml"]}}:
        errors.append(
            "automatic publication must trigger only on trusted main pyproject pushes"
        )
    if workflow.get("permissions") != READ_ONLY_PERMISSIONS:
        errors.append("automatic release permissions must default to contents: read")
    errors.extend(
        _validate_concurrency(
            workflow,
            expected_group="automatic-release-${{ github.ref }}",
            expected_cancellation=False,
        )
    )
    jobs = _mapping(workflow.get("jobs")) or {}
    expected_jobs = {
        "detect",
        RELEASE_PRODUCER_JOB,
        "create-release",
        "publish",
        "container",
    }
    if set(jobs) != expected_jobs:
        errors.append("automatic release jobs do not match the exact contract")
    expected_permissions = {
        "create-release": {"contents": "write"},
        "publish": {"id-token": "write"},
        "container": {
            "actions": "read",
            "attestations": "write",
            "contents": "write",
            "id-token": "write",
            "packages": "write",
        },
    }
    for name, job_value in jobs.items():
        job = _mapping(job_value) or {}
        condition = job.get("if")
        if not isinstance(condition, str) or not all(
            clause in condition
            for clause in (
                TRUSTED_REPOSITORY_CONDITION,
                "github.event_name == 'push'",
                "github.ref == 'refs/heads/main'",
            )
        ):
            errors.append(
                f"automatic release job {name!r} lacks the trusted-main guard"
            )
        permissions = job.get("permissions")
        if name in expected_permissions:
            if permissions != expected_permissions[name]:
                errors.append(
                    f"automatic release job {name!r} permissions are not minimal"
                )
        elif permissions is not None:
            errors.append(
                f"automatic release job {name!r} must inherit read-only permissions"
            )
    publish = _mapping(jobs.get("publish")) or {}
    if publish.get("environment") != "pypi":
        errors.append("automatic PyPI publication must use the pypi environment")
    container = _mapping(jobs.get("container")) or {}
    if container.get("uses") != "./.github/workflows/container-release.yml":
        errors.append("container publication must call the reviewed reusable workflow")
    if re.search(r"\bsecrets\b", text, re.IGNORECASE):
        errors.append("automatic release workflow must not access stored secrets")
    required_contracts = (
        "scripts.release.detect_version",
        "scripts.release.check_changelog",
        "github.event.before",
        "needs.detect.outputs.changed == 'true'",
        "/git/ref/tags/$RELEASE_TAG",
        "/releases/tags/$RELEASE_TAG",
        "/pypi/markweave/$RELEASE_VERSION/json",
        '--method POST "repos/$GITHUB_REPOSITORY/git/refs"',
        '--field "ref=refs/tags/$RELEASE_TAG"',
        '--field "sha=$SOURCE_SHA"',
        "target_commitish=$SOURCE_SHA",
        "python-release-${{ needs.detect.outputs.tag }}",
        '--version "$RELEASE_VERSION"',
        "./.github/workflows/container-release.yml",
    )
    for required in required_contracts:
        if required not in text:
            errors.append(f"automatic release is missing contract: {required}")
    detect_steps = _job_steps(workflow, "detect")
    step_names = [step.get("name") for step in detect_steps]
    required_order = (
        "Detect an exact final-version transition",
        "Require a changelog entry for a material version transition",
        "Reject an existing tag, release, or PyPI version",
    )
    try:
        indices = [step_names.index(name) for name in required_order]
    except ValueError:
        errors.append(
            "automatic release must gate remote checks on changelog validation"
        )
    else:
        if indices != sorted(indices):
            errors.append(
                "automatic release must gate remote checks on changelog validation"
            )
    create_steps = [
        step
        for step in _job_steps(workflow, "create-release")
        if step.get("name") == "Create the exact tag and published GitHub Release"
    ]
    create_run = create_steps[0].get("run") if len(create_steps) == 1 else None
    tag_post = '--method POST "repos/$GITHUB_REPOSITORY/git/refs"'
    release_post = '--method POST "repos/$GITHUB_REPOSITORY/releases"'
    exact_tag_check = (
        'gh api "repos/$GITHUB_REPOSITORY/git/ref/tags/$RELEASE_TAG" --jq .object.sha'
    )
    exact_release_check = (
        "--jq '[.tag_name, .target_commitish, .draft, .prerelease] | @tsv'"
    )
    if not isinstance(create_run, str):
        errors.append("automatic release lacks the unique atomic creation step")
    elif (
        tag_post not in create_run
        or release_post not in create_run
        or create_run.index(tag_post) > create_run.index(release_post)
        or create_run.count(exact_tag_check) < PARTIAL_TAG_CHECK_COUNT
        or exact_release_check not in create_run
    ):
        errors.append(
            "automatic release must atomically create and exactly verify tag before Release"
        )
    errors.extend(
        _validate_action_allowlist(
            workflow,
            allowed_actions=frozenset(
                {
                    "actions/checkout",
                    "actions/download-artifact",
                    "actions/setup-python",
                    "actions/upload-artifact",
                    "astral-sh/setup-uv",
                    "pypa/gh-action-pypi-publish",
                }
            ),
            allowed_local_workflows=frozenset(
                {"./.github/workflows/container-release.yml"}
            ),
        )
    )
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
        if path.name == "container-release.yml":
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as error:
                errors.append(f"{path}: cannot read workflow: {error}")
                continue
            errors.extend(
                f"{path}: {error}"
                for error in validate_container_release_workflow_text(text)
            )
            continue
        if path.name == "release.yml":
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as error:
                errors.append(f"{path}: cannot read workflow: {error}")
                continue
            errors.extend(
                f"{path}: {error}"
                for error in validate_production_release_workflow_text(text)
            )
            continue
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
    required_workflows = {
        *READ_ONLY_WORKFLOW_POLICIES,
        "container-release.yml",
        "release.yml",
    }
    missing_workflows = required_workflows.difference(
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
