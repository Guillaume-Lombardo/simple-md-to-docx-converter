from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml


@pytest.mark.unit
def test_workspace_excludes_the_isolated_mermaid_toolchain() -> None:
    workspace = yaml.safe_load(Path("pnpm-workspace.yaml").read_text(encoding="utf-8"))
    assert workspace["packages"] == [".", "web", "!spikes/toolchain"]
    assert Path("spikes/toolchain/package-lock.json").is_file()
    assert not Path("package-lock.json").exists()
    assert not Path("web/package-lock.json").exists()


@pytest.mark.unit
def test_lock_preserves_the_audited_npm_package_versions_and_integrities() -> None:
    lock = yaml.safe_load(Path("pnpm-lock.yaml").read_text(encoding="utf-8"))
    package_versions: set[tuple[str, str]] = set()
    for key, package in lock["packages"].items():
        identity = str(key).split("(", 1)[0]
        name, version = identity.rsplit("@", 1)
        package_versions.add((name.lstrip("/"), version))
        assert package["resolution"]["integrity"].startswith("sha512-")
    serialized = "\n".join(
        f"{name}@{version}" for name, version in sorted(package_versions)
    )
    assert len(package_versions) == 610
    assert hashlib.sha256(serialized.encode()).hexdigest() == (
        "472524d7c110193275295a9edaadc0bd5492a9d073af47ecc8b433b3daf78a93"
    )
    assert hashlib.sha256(
        Path("spikes/toolchain/package-lock.json").read_bytes()
    ).hexdigest() == (
        "6fc7bf6f32bd3f3108c0955e8994c019c04cd9964b9c50472aa28474e9d7e73f"
    )


@pytest.mark.unit
def test_package_manager_bootstrap_is_exact_and_network_fenced() -> None:
    manifest = json.loads(Path("package.json").read_text(encoding="utf-8"))
    bootstrap = Path("scripts/javascript/bootstrap-pnpm.sh").read_text(encoding="utf-8")
    assert manifest["packageManager"] == (
        "pnpm@11.25.0+sha224.c69bc375107d8eef668fbe1ebab8b3a34253dc594dff6a0a36d8a16c"
    )
    for contract in (
        "corepack_version=0.36.0",
        "registry.npmjs.org/corepack/-/corepack-${corepack_version}.tgz",
        "openssl dgst -sha512 -binary",
        "COREPACK_ENABLE_NETWORK=0",
        'test "$("$install_directory/bin/corepack" --version)" = "$corepack_version"',
    ):
        assert contract in bootstrap


@pytest.mark.unit
def test_frontend_container_uses_frozen_root_workspace_and_pruned_graph() -> None:
    containerfile = Path("web/Containerfile").read_text(encoding="utf-8")
    for contract in (
        "COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./",
        "pnpm install --frozen-lockfile --ignore-scripts --filter @markweave/web...",
        "pnpm --filter @markweave/web deploy --prod --legacy",
        "/opt/markweave-web-production/node_modules",
    ):
        assert contract in containerfile
    runtime = containerfile.split("FROM ${RUNTIME_IMAGE} AS runtime", 1)[1]
    assert "corepack" not in runtime.casefold()
    assert "pnpm" not in runtime.casefold()


@pytest.mark.unit
def test_release_recovery_supports_pnpm_and_historical_npm_locks() -> None:
    workflow = Path(".github/workflows/container-release.yml").read_text(
        encoding="utf-8"
    )
    assert 'git cat-file -e "$SOURCE_SHA:pnpm-lock.yaml"' in workflow
    assert 'git show "$SOURCE_SHA:pnpm-lock.yaml"' in workflow
    assert 'git show "$SOURCE_SHA:web/package-lock.json"' in workflow
    publisher = Path("scripts/container/publish-release-pair.sh").read_text(
        encoding="utf-8"
    )
    assert "${4:-pnpm-lock.yaml}" in publisher


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(repository: Path, subject: str, content: str) -> str:
    (repository / "state.txt").write_text(content, encoding="utf-8")
    _git(repository, "add", "state.txt")
    _git(repository, "commit", "-m", subject)
    return _git(repository, "rev-parse", "HEAD")


def _run_candidate_selector(
    repository: Path, candidate: str, baseline: str, reviewed_merge: str
) -> tuple[str, ...]:
    selector = Path("scripts/javascript/select-t67-rollback-commits.sh").resolve()
    completed = subprocess.run(
        ["bash", str(selector), candidate, baseline, reviewed_merge],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip())
    return tuple(completed.stdout.splitlines())


def _synthetic_candidate(repository: Path) -> tuple[str, str, str]:
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "T67 Test")
    _git(repository, "config", "user.email", "t67@example.invalid")
    baseline = _commit(repository, "chore: npm baseline", "npm\n")
    first = _commit(repository, "chore(T67): migrate workspace", "pnpm\n")
    _git(repository, "checkout", "-b", "planning", baseline)
    (repository / "planning.txt").write_text("planning\n", encoding="utf-8")
    _git(repository, "add", "planning.txt")
    _git(repository, "commit", "-m", "docs(T69): planning")
    _git(repository, "checkout", "main")
    _git(repository, "merge", "--no-ff", "planning", "-m", "Merge planning")
    reviewed_merge = _git(repository, "rev-parse", "HEAD")
    candidate = _commit(repository, "test(T67): validate workspace", "pnpm ready\n")
    assert first != candidate
    return baseline, candidate, reviewed_merge


@pytest.mark.unit
def test_rollback_candidate_selection_uses_explicit_synthetic_history(
    tmp_path: Path,
) -> None:
    baseline, candidate, reviewed_merge = _synthetic_candidate(tmp_path)

    selected = _run_candidate_selector(tmp_path, candidate, baseline, reviewed_merge)

    assert len(selected) == 2
    assert selected[-1] == candidate


@pytest.mark.unit
def test_rollback_candidate_selection_ignores_checkout_merge_ref_and_future_history(
    tmp_path: Path,
) -> None:
    baseline, candidate, reviewed_merge = _synthetic_candidate(tmp_path)
    _git(tmp_path, "checkout", "-b", "future-base", baseline)
    (tmp_path / "later.txt").write_text("later main\n", encoding="utf-8")
    _git(tmp_path, "add", "later.txt")
    _git(tmp_path, "commit", "-m", "docs: later main change")
    _git(tmp_path, "merge", "--no-ff", candidate, "-m", "Synthetic pull request merge")
    merge_ref = _git(tmp_path, "rev-parse", "HEAD")

    selected = _run_candidate_selector(tmp_path, candidate, baseline, reviewed_merge)
    assert selected[-1] == candidate
    with pytest.raises(ValueError, match="unrelated"):
        _run_candidate_selector(tmp_path, merge_ref, baseline, reviewed_merge)

    _git(tmp_path, "checkout", "-b", "future-pr", candidate)
    future = _commit(tmp_path, "feat: future frontend work", "future pnpm\n")
    assert (
        _run_candidate_selector(tmp_path, candidate, baseline, reviewed_merge)
        == selected
    )
    with pytest.raises(ValueError, match="unrelated"):
        _run_candidate_selector(tmp_path, future, baseline, reviewed_merge)


@pytest.mark.unit
def test_rollback_contract_covers_every_migration_surface() -> None:
    rehearsal = Path("scripts/javascript/rehearse-npm-rollback.sh").read_text(
        encoding="utf-8"
    )
    selector = Path("scripts/javascript/select-t67-rollback-commits.sh").read_text(
        encoding="utf-8"
    )
    for contract in (
        'rev-list --first-parent --reverse "$baseline..$candidate"',
        "unrelated first-parent commit in candidate range",
        "unrelated merge commit in candidate range",
        "baseline is not the direct npm parent of the T67 series",
    ):
        assert contract in selector
    for contract in (
        "select-t67-rollback-commits.sh",
        "rollback did not restore exact baseline bytes",
        "package-lock.json",
        "web/package-lock.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "docs/package-management.md",
        "scripts/javascript/bootstrap-pnpm.sh",
        "scripts/javascript/select-t67-rollback-commits.sh",
        "cache: npm",
        "COPY package.json package-lock.json ./",
        "npm run build && npm prune --omit=dev --ignore-scripts",
        "PUPPETEER_SKIP_DOWNLOAD=true npm ci --ignore-scripts",
        "npm ci --prefix spikes/toolchain --omit=dev --ignore-scripts",
        "grep -RIE '(pnpm|corepack)'",
    ):
        assert contract in rehearsal


@pytest.mark.unit
def test_hosted_benchmark_collects_comparable_raw_evidence() -> None:
    benchmark = Path("scripts/javascript/benchmark-package-managers.sh").read_text(
        encoding="utf-8"
    )
    for contract in (
        "for sample in 1 2 3",
        "cold-install",
        "warm-install",
        "frontend-build",
        "cache_archive_bytes",
        "node_modules_bytes",
        "runner_image=",
        "node --version",
        "raw.log",
        "podman image inspect",
        "manifest-lock-sha256.txt",
    ):
        assert contract in benchmark
    assert (
        benchmark.count("final-image 1 podman build --no-cache --format oci --tag") == 2
    )
    assert benchmark.count("image: podman build --no-cache --format oci") == 2
