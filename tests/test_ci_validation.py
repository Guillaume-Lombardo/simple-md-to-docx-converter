"""Tests for repository-local CI security validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.validate_ci import (
    discover_workflow_paths,
    main,
    validate_python_imports,
    validate_registry_text,
    validate_release_workflow_text,
    validate_workflow_files,
    validate_workflow_text,
)

FULL_SHA_A = "a" * 40
FULL_SHA_B = "b" * 40
FULL_SHA_C = "c" * 40
FULL_SHA_D = "d" * 40
FULL_SHA_E = "e" * 40


@pytest.fixture
def valid_release_workflow() -> str:
    """Describe a future release without selecting its production trigger policy."""
    return f"""\
name: Python release

on:
  release:
    types: [published]

permissions:
  contents: read

concurrency:
  group: release-${{{{ github.ref }}}}
  cancel-in-progress: false

jobs:
  build-and-verify:
    if: ${{{{ github.repository == 'Guillaume-Lombardo/simple-md-to-docx-converter' }}}}
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    steps:
      - name: Check out reviewed source
        uses: actions/checkout@{FULL_SHA_A}
        with:
          persist-credentials: false
      - name: Set up Python
        uses: actions/setup-python@{FULL_SHA_B}
      - name: Set up uv
        uses: astral-sh/setup-uv@{FULL_SHA_C}
      - name: Build and verify exactly once
        run: uv run python -m scripts.release.build --output dist
      - name: Transfer verified artifacts
        uses: actions/upload-artifact@{FULL_SHA_D}
        with:
          name: verified-python-distributions
          path: dist
  publish:
    needs: build-and-verify
    if: ${{{{ github.repository == 'Guillaume-Lombardo/simple-md-to-docx-converter' }}}}
    runs-on: ubuntu-24.04
    timeout-minutes: 10
    environment: pypi
    permissions:
      id-token: write
    steps:
      - name: Download verified artifacts
        uses: actions/download-artifact@{FULL_SHA_D}
        with:
          name: verified-python-distributions
          path: dist
      - name: Publish exact artifacts
        uses: pypa/gh-action-pypi-publish@{FULL_SHA_E}
        with:
          packages-dir: dist/
          attestations: true
"""


@pytest.mark.unit
def test_committed_workflow_satisfies_local_security_policy() -> None:
    """The real workflow is covered by the same validator used in CI."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert validate_workflow_text(workflow) == []


@pytest.mark.unit
def test_document_engine_job_installs_checksum_locked_document_engines() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "if: ${{ matrix.domain == 'document-engines' }}" in workflow
    assert (
        "https://github.com/jgm/pandoc/releases/download/3.10.2/"
        "pandoc-3.10.2-linux-amd64.tar.gz"
    ) in workflow
    assert (
        "c7edd535941c48be6a362081a748272837de81ae11777202d9c341d3d8261c9a" in workflow
    )
    assert "sha256sum --check --strict" in workflow
    assert (
        "878e5ab495b8a694980fca61bc09b37e651ccedce2291c73434d16e48a2646fd" in workflow
    )
    assert (
        "6fc7bf6f32bd3f3108c0955e8994c019c04cd9964b9c50472aa28474e9d7e73f" in workflow
    )
    assert 'PUPPETEER_SKIP_DOWNLOAD: "true"' in workflow
    assert "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020" in workflow
    assert "node-version: 22.23.1" in workflow
    assert 'test "$(node --version)" = "v22.23.1"' in workflow
    assert "npm ci --prefix spikes/toolchain --omit=dev --ignore-scripts" in workflow
    assert "mmdc --version" in workflow
    assert "google-chrome-stable --version" in workflow
    assert "npm run test:web-browser" in workflow
    assert "awk '{$1=$1; print}'" in workflow


@pytest.mark.unit
def test_validator_rejects_removed_real_browser_workflow() -> None:
    """Pinned module tests cannot stand in for the real Chrome acceptance workflow."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    weakened = workflow.replace("npm run test:web-browser", "npm run test:web")
    assert any(
        "test:web-browser" in error for error in validate_workflow_text(weakened)
    )


@pytest.mark.unit
def test_e2e_matrix_installs_rootless_runtime_and_retains_only_failures() -> None:
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert (
        "matrix.domain == 'container' || startsWith(matrix.domain, 'e2e-')" in workflow
    )
    assert (
        "matrix.domain == 'document-engines' || startsWith(matrix.domain, 'e2e-')"
        in workflow
    )
    assert "failure() && startsWith(matrix.domain, 'e2e-')" in workflow
    assert (
        "artifacts/e2e/${{ matrix.domain == 'e2e-standalone' "
        "&& 'standalone' || 'distributed' }}" in workflow
    )


@pytest.mark.unit
def test_validator_rejects_successful_e2e_artifact_retention() -> None:
    """Browser and service evidence must not be uploaded from passing scenarios."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    weakened = workflow.replace(
        "failure() && startsWith(matrix.domain, 'e2e-')",
        "always() && startsWith(matrix.domain, 'e2e-')",
    )
    assert any(
        "failure() && startsWith" in error for error in validate_workflow_text(weakened)
    )


@pytest.mark.unit
def test_validator_rejects_unpinned_action_and_secret_access() -> None:
    """Mutable action tags and secret reads are rejected together."""
    workflow = """\
jobs:
  check:
    steps:
      - uses: actions/checkout@v7
        env:
          TOKEN: ${{ secrets.DEPLOY_TOKEN }}
"""
    errors = validate_workflow_text(workflow)
    assert "every action reference must be pinned" in " ".join(errors)
    assert "forbidden workflow secret access" in errors


@pytest.mark.unit
def test_validator_rejects_short_hex_revision_and_missing_timeout() -> None:
    """Hex-looking abbreviations and unbounded jobs remain invalid pins."""
    workflow = "jobs:\n  check:\n    uses: owner/action@abc\n"
    errors = validate_workflow_text(workflow)
    assert "action revision is not a full commit SHA: 'abc'" in errors
    assert "every job must define a bounded timeout" in errors


@pytest.mark.unit
def test_validator_rejects_any_write_permission() -> None:
    """The validation-only workflow cannot acquire repository write access."""
    assert "write permission is forbidden in the CI workflow" in validate_workflow_text(
        "permissions:\n  checks: write\n"
    )


@pytest.mark.unit
def test_validator_rejects_broad_cache_writes() -> None:
    """Pull requests, merge groups, forks, releases, and manual runs remain restore-only."""
    errors = validate_workflow_text(
        "save-cache: ${{ github.event_name != 'pull_request' }}\n"
    )
    assert "cache writes must be limited to trusted pushes on main" in errors


@pytest.mark.unit
def test_gate_rejects_skipped_active_domain() -> None:
    """A selected active domain cannot be accepted as planned or skipped by the gate."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    weakened = workflow.replace(
        'if [[ "$RUNNABLE_DOMAINS" == "[]" ]]; then',
        'if [[ "$HEAVY_RESULT" == "skipped" ]]; then',
    )
    assert any(
        "RUNNABLE_DOMAINS" in error for error in validate_workflow_text(weakened)
    )


@pytest.mark.unit
def test_validator_rejects_removed_changed_line_coverage() -> None:
    """Pull requests cannot bypass the changed application line threshold."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    weakened = workflow.replace(
        "python -m scripts.ci.check_changed_coverage",
        "python -m scripts.ci.validate_ci",
    )
    assert any(
        "check_changed_coverage" in error for error in validate_workflow_text(weakened)
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("command", "replacement"),
    [
        ("uv run pytest -m unit", "echo uv run pytest -m unit"),
        ("uv run pytest -m unit", "true # uv run pytest -m unit"),
        (
            "uv run pytest -m unit",
            "COMMAND='uv run pytest -m unit'; $COMMAND",
        ),
        (
            "python -m scripts.ci.check_changed_coverage",
            "echo python -m scripts.ci.check_changed_coverage",
        ),
    ],
)
def test_ci_contract_rejects_inert_or_indirect_commands(
    command: str, replacement: str
) -> None:
    """Echoes, comments, and variables cannot satisfy an executable CI contract."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    weakened = workflow.replace(command, replacement, 1)
    assert validate_workflow_text(weakened)


@pytest.mark.unit
def test_validator_rejects_removed_branch_only_coverage() -> None:
    """Combined coverage cannot replace the independent branch ratio gate."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    weakened = workflow.replace(
        "python -m scripts.ci.check_branch_coverage",
        "python -m scripts.ci.validate_ci",
    )
    assert any(
        "check_branch_coverage" in error for error in validate_workflow_text(weakened)
    )


@pytest.mark.unit
def test_validator_rejects_duplicate_required_gate_name() -> None:
    """Branch protection must observe one unambiguous required check context."""
    workflow = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")
    weakened = workflow.replace("name: CI / affected domains", "name: CI / gate")
    errors = validate_workflow_text(weakened)
    assert "workflow must define exactly one CI / gate check" in errors


@pytest.mark.unit
def test_validator_rejects_direct_unittest_mock_import(tmp_path: Path) -> None:
    """Tests must use pytest-mock instead of importing unittest.mock."""
    source = tmp_path / "unsafe.py"
    source.write_text("from unittest.mock import patch\n", encoding="utf-8")
    assert validate_python_imports([source]) == [
        f"direct unittest.mock import in {source}"
    ]


@pytest.mark.unit
def test_registry_validator_rejects_invalid_json() -> None:
    """A malformed lifecycle registry fails cheap security validation."""
    assert validate_registry_text("{")[0].startswith("invalid domain registry JSON:")


@pytest.mark.unit
def test_registry_validator_rejects_partial_domain_set() -> None:
    """Cheap validation prevents a selector/registry mismatch."""
    assert validate_registry_text("{}") == [
        "domain registry does not match the selector's known domains"
    ]


@pytest.mark.unit
def test_committed_ci_validation_entrypoint_succeeds() -> None:
    """The cheap security command used by the workflow succeeds end to end."""
    assert main() == 0


@pytest.mark.unit
def test_all_committed_workflows_have_explicit_policies() -> None:
    """The entrypoint cannot silently leave a newly committed workflow unchecked."""
    workflows = discover_workflow_paths(Path(".github/workflows"))
    assert [path.name for path in workflows] == ["ci.yml", "mutation.yml"]
    assert validate_workflow_files(workflows) == []


@pytest.mark.unit
def test_workflow_scan_rejects_unknown_policy(tmp_path: Path) -> None:
    """Adding a workflow requires a deliberate filename-specific policy."""
    workflow = tmp_path / "unexpected.yml"
    workflow.write_text("name: Unexpected\n", encoding="utf-8")
    assert validate_workflow_files([workflow]) == [
        f"{workflow}: workflow has no explicit security policy"
    ]


@pytest.mark.unit
def test_workflow_scan_includes_both_supported_yaml_extensions(tmp_path: Path) -> None:
    """A workflow cannot evade policy selection by using the long YAML suffix."""
    short = tmp_path / "short.yml"
    long = tmp_path / "long.yaml"
    short.touch()
    long.touch()
    assert discover_workflow_paths(tmp_path) == [long, short]


@pytest.mark.unit
def test_mutation_policy_rejects_trigger_permission_and_job_drift() -> None:
    """The maintenance workflow remains exactly bounded and read-only."""
    workflow = Path(".github/workflows/mutation.yml").read_text(encoding="utf-8")
    weakened = workflow.replace("  schedule:\n", "  pull_request:\n", 1).replace(
        "permissions:\n  contents: read",
        "permissions:\n  contents: write",
        1,
    )
    weakened = weakened.replace("    timeout-minutes: 30", "    timeout-minutes: 31")
    errors = validate_workflow_text(weakened, workflow_name="mutation.yml")
    assert "workflow triggers do not match the explicit allowlist" in errors
    assert "read-only workflow permissions must be exactly contents: read" in errors
    assert "job 'mutation' must define an allowlisted bounded timeout" in errors


@pytest.mark.unit
def test_mutation_policy_rejects_arbitrary_concurrency_expression() -> None:
    """Cancellation semantics are part of the filename-specific policy."""
    workflow = Path(".github/workflows/mutation.yml").read_text(encoding="utf-8")
    weakened = workflow.replace(
        "cancel-in-progress: true", "cancel-in-progress: attacker-controlled"
    )
    assert (
        "workflow cancel-in-progress does not match the explicit policy"
        in validate_workflow_text(weakened, workflow_name="mutation.yml")
    )


@pytest.mark.unit
def test_read_only_policy_rejects_unallowlisted_action_and_privileged_command() -> None:
    """A pinned action or container command is not trusted merely because it is valid YAML."""
    workflow = Path(".github/workflows/mutation.yml").read_text(encoding="utf-8")
    weakened = workflow.replace(
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        f"third-party/unknown@{FULL_SHA_A}",
    ).replace("uv sync --locked --all-groups", "docker run --privileged image")
    errors = validate_workflow_text(weakened, workflow_name="mutation.yml")
    assert any("action is not allowlisted" in error for error in errors)
    assert "forbidden workflow fragment: '--privileged'" in errors


@pytest.mark.unit
@pytest.mark.parametrize("flow_style", [False, True])
def test_action_pin_validation_uses_parsed_yaml(flow_style: bool) -> None:
    """Whitespace and flow mappings cannot hide a mutable action reference."""
    workflow = Path(".github/workflows/mutation.yml").read_text(encoding="utf-8")
    pinned = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
    if flow_style:
        weakened = workflow.replace(
            "      - name: Check out reviewed source\n"
            f"        uses: {pinned} # v7.0.1\n"
            "        with:\n"
            "          persist-credentials: false",
            "      - {uses: actions/checkout@v7, with: {persist-credentials: false}}",
        )
    else:
        weakened = workflow.replace(f"uses: {pinned}", "uses : actions/checkout@v7")
    errors = validate_workflow_text(weakened, workflow_name="mutation.yml")
    assert any("every action reference must be pinned" in error for error in errors)


@pytest.mark.unit
def test_release_policy_accepts_caller_approved_trusted_context(
    valid_release_workflow: str,
) -> None:
    """A caller-supplied policy can validate the future isolated release shape."""
    assert (
        validate_release_workflow_text(
            valid_release_workflow, approved_triggers=frozenset({"release"})
        )
        == []
    )


@pytest.mark.unit
def test_release_policy_rejects_untrusted_dispatch(
    valid_release_workflow: str,
) -> None:
    """Manual dispatch cannot be declared trusted by a permissive caller."""
    dispatched = valid_release_workflow.replace(
        "  release:\n    types: [published]", "  workflow_dispatch:"
    )
    errors = validate_release_workflow_text(
        dispatched, approved_triggers=frozenset({"workflow_dispatch"})
    )
    assert "approved release triggers must contain only trusted contexts" in errors


@pytest.mark.unit
def test_release_policy_rejects_unsafe_event_or_push_shapes(
    valid_release_workflow: str,
) -> None:
    """Either future trigger family must remain restricted to a release boundary."""
    draft_event = valid_release_workflow.replace(
        "types: [published]", "types: [created]"
    )
    assert (
        "release event trigger must be restricted to published releases"
        in validate_release_workflow_text(
            draft_event, approved_triggers=frozenset({"release"})
        )
    )
    branch_push = valid_release_workflow.replace(
        "  release:\n    types: [published]",
        "  push:\n    branches: [main]",
    )
    assert (
        "push release trigger must be restricted to an explicit tag policy"
        in validate_release_workflow_text(
            branch_push, approved_triggers=frozenset({"push"})
        )
    )


@pytest.mark.unit
@pytest.mark.parametrize("tags", ["[true]", '["  "]'])
def test_release_policy_rejects_non_string_or_empty_tag_patterns(
    valid_release_workflow: str, tags: str
) -> None:
    """A caller-approved push still requires explicit non-empty string patterns."""
    pushed = valid_release_workflow.replace(
        "  release:\n    types: [published]", f"  push:\n    tags: {tags}"
    )
    errors = validate_release_workflow_text(
        pushed, approved_triggers=frozenset({"push"})
    )
    assert "push release trigger must be restricted to an explicit tag policy" in errors


@pytest.mark.unit
def test_release_policy_rejects_cancellable_publication(
    valid_release_workflow: str,
) -> None:
    """A later run cannot interrupt a protected publication midway."""
    weakened = valid_release_workflow.replace(
        "cancel-in-progress: false", "cancel-in-progress: true"
    )
    errors = validate_release_workflow_text(
        weakened, approved_triggers=frozenset({"release"})
    )
    assert "release publication concurrency must not cancel in progress" in errors


@pytest.mark.unit
@pytest.mark.parametrize(
    ("needle", "replacement", "message"),
    [
        (
            "environment: pypi",
            "environment: staging",
            "publish job must use the protected pypi environment",
        ),
        (
            "permissions:\n      id-token: write",
            "permissions:\n      contents: read",
            "publish job permissions must be exactly id-token: write",
        ),
        (
            "timeout-minutes: 10",
            "timeout-minutes: 0",
            "release job 'publish' must have a bounded timeout",
        ),
        (
            "needs: build-and-verify",
            "needs: []",
            "publish job must depend on prior artifact validation",
        ),
        (
            "needs: build-and-verify",
            "needs: unrelated",
            "publish job must depend on the verified artifact uploader",
        ),
        (
            "runs-on: ubuntu-24.04\n    timeout-minutes: 10",
            "runs-on: self-hosted\n    timeout-minutes: 10",
            "release job 'publish' must use the allowlisted hosted runner",
        ),
        (
            "environment: pypi",
            "environment: pypi\n    container: attacker/image",
            "publish job contains fields outside the minimal allowlist",
        ),
    ],
)
def test_release_policy_rejects_privilege_and_gate_weakening(
    valid_release_workflow: str,
    needle: str,
    replacement: str,
    message: str,
) -> None:
    """Publication keeps its environment, token scope, deadline, and dependency gate."""
    errors = validate_release_workflow_text(
        valid_release_workflow.replace(needle, replacement, 1),
        approved_triggers=frozenset({"release"}),
    )
    assert message in errors


@pytest.mark.unit
def test_release_policy_rejects_secret_credentials(valid_release_workflow: str) -> None:
    """Trusted Publishing cannot be weakened with a repository secret."""
    weakened = valid_release_workflow.replace(
        "          attestations: true",
        "          attestations: true\n          password: ${{ secrets.PYPI_TOKEN }}",
    )
    errors = validate_release_workflow_text(
        weakened, approved_triggers=frozenset({"release"})
    )
    assert "forbidden workflow secret access" in errors
    assert "PyPI publication must use Trusted Publishing without credentials" in errors


@pytest.mark.unit
@pytest.mark.parametrize(
    "expression",
    [
        "${{ secrets['PYPI_TOKEN'] }}",
        "${{ secrets ['PYPI_TOKEN'] }}",
        "${{ github['token'] }}",
    ],
)
def test_release_policy_rejects_bracket_secret_and_token_access(
    valid_release_workflow: str, expression: str
) -> None:
    """Expression indexing cannot evade the no-credential release policy."""
    weakened = valid_release_workflow.replace(
        "    timeout-minutes: 30",
        f"    timeout-minutes: 30\n    env:\n      TOKEN: {expression}",
        1,
    )
    errors = validate_release_workflow_text(
        weakened, approved_triggers=frozenset({"release"})
    )
    assert any("access" in error for error in errors)


@pytest.mark.unit
def test_release_policy_rejects_publish_rebuild_or_extra_step(
    valid_release_workflow: str,
) -> None:
    """The OIDC job only downloads and uploads the already verified bytes."""
    weakened = valid_release_workflow.replace(
        "      - name: Publish exact artifacts",
        "      - name: Rebuild\n"
        "        run: uv build\n"
        "      - name: Publish exact artifacts",
    )
    errors = validate_release_workflow_text(
        weakened, approved_triggers=frozenset({"release"})
    )
    assert "release workflow must invoke the distribution build exactly once" in errors
    assert "publish job must contain exactly two action steps" in errors


@pytest.mark.unit
@pytest.mark.parametrize(
    "replacement",
    [
        "run: echo scripts.release.build",
        "run: true # scripts.release.build",
        "run: BUILD=scripts.release.build; echo $BUILD",
    ],
)
def test_release_policy_rejects_inert_or_indirect_build(
    valid_release_workflow: str, replacement: str
) -> None:
    """Echoes, comments, and variables cannot satisfy the build-once contract."""
    weakened = valid_release_workflow.replace(
        "run: uv run python -m scripts.release.build --output dist",
        replacement,
    )
    errors = validate_release_workflow_text(
        weakened, approved_triggers=frozenset({"release"})
    )
    assert "release workflow must invoke the distribution build exactly once" in errors


@pytest.mark.unit
def test_release_policy_rejects_artifact_substitution(
    valid_release_workflow: str,
) -> None:
    """Publication cannot select bytes other than the uniquely transferred bundle."""
    weakened = valid_release_workflow.replace(
        "packages-dir: dist/", "packages-dir: other/"
    )
    errors = validate_release_workflow_text(
        weakened, approved_triggers=frozenset({"release"})
    )
    assert "PyPI upload must publish only the downloaded artifact directory" in errors


@pytest.mark.unit
def test_release_policy_rejects_missing_repository_guard(
    valid_release_workflow: str,
) -> None:
    """Every release job is tied to the intended upstream repository."""
    weakened = valid_release_workflow.replace(
        "    if: ${{ github.repository == "
        "'Guillaume-Lombardo/simple-md-to-docx-converter' }}\n",
        "",
        1,
    )
    errors = validate_release_workflow_text(
        weakened, approved_triggers=frozenset({"release"})
    )
    assert "release job 'build-and-verify' lacks the trusted repository guard" in errors


@pytest.mark.unit
def test_release_policy_rejects_bypassable_repository_guard(
    valid_release_workflow: str,
) -> None:
    """A trusted comparison cannot be neutralized by an always-true disjunction."""
    weakened = valid_release_workflow.replace(
        "simple-md-to-docx-converter' }}",
        "simple-md-to-docx-converter' || true }}",
        1,
    )
    errors = validate_release_workflow_text(
        weakened, approved_triggers=frozenset({"release"})
    )
    assert "release job 'build-and-verify' lacks the trusted repository guard" in errors


@pytest.mark.unit
def test_release_policy_rejects_negated_repository_guard(
    valid_release_workflow: str,
) -> None:
    """The trusted comparison cannot be accepted inside a negated expression."""
    weakened = valid_release_workflow.replace(
        "github.repository == 'Guillaume-Lombardo/simple-md-to-docx-converter'",
        "!(github.repository == 'Guillaume-Lombardo/simple-md-to-docx-converter')",
        1,
    )
    errors = validate_release_workflow_text(
        weakened, approved_triggers=frozenset({"release"})
    )
    assert "release job 'build-and-verify' lacks the trusted repository guard" in errors


@pytest.mark.unit
def test_release_policy_rejects_mutable_action(valid_release_workflow: str) -> None:
    """Release actions follow the same immutable full-SHA policy as CI."""
    weakened = valid_release_workflow.replace(
        f"pypa/gh-action-pypi-publish@{FULL_SHA_E}",
        "pypa/gh-action-pypi-publish@release/v1",
    )
    errors = validate_release_workflow_text(
        weakened, approved_triggers=frozenset({"release"})
    )
    assert any("every action reference must be pinned" in error for error in errors)


@pytest.mark.unit
def test_release_policy_rejects_mutable_action_with_spaced_key(
    valid_release_workflow: str,
) -> None:
    """Release pinning is based on parsed action fields, not lexical layout."""
    weakened = valid_release_workflow.replace(
        f"uses: pypa/gh-action-pypi-publish@{FULL_SHA_E}",
        "uses : pypa/gh-action-pypi-publish@release/v1",
    )
    errors = validate_release_workflow_text(
        weakened, approved_triggers=frozenset({"release"})
    )
    assert any("every action reference must be pinned" in error for error in errors)


@pytest.mark.unit
def test_validator_rejects_duplicate_yaml_keys() -> None:
    """A duplicate security key cannot exploit parser interpretation differences."""
    errors = validate_workflow_text(
        "permissions:\n  contents: read\npermissions:\n  contents: write\n"
    )
    assert any("found duplicate key 'permissions'" in error for error in errors)
