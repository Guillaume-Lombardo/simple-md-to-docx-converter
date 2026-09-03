from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

NPM_BASELINE = "1594128bc84290df3699390643c729ef9d5d6d30"


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


def _write_rollback_tool_stubs(directory: Path) -> Path:
    directory.mkdir()
    node = directory / "node"
    node.write_text("#!/bin/sh\nprintf '%s\\n' v24.19.0\n", encoding="utf-8")
    npm = directory / "npm"
    npm.write_text(
        "#!/bin/sh\n"
        'if [ "${1:-}" = --version ]; then\n'
        "  printf '%s\\n' 11.17.0\n"
        "else\n"
        '  printf \'%s\\n\' "$*" >> "$ROLLBACK_NPM_LOG"\n'
        "fi\n",
        encoding="utf-8",
    )
    node.chmod(node.stat().st_mode | stat.S_IXUSR)
    npm.chmod(npm.stat().st_mode | stat.S_IXUSR)
    return directory


@pytest.mark.unit
def test_rollback_rehearses_the_candidate_series_from_its_exact_npm_parent(
    tmp_path: Path,
) -> None:
    tools = _write_rollback_tool_stubs(tmp_path / "bin")
    log = tmp_path / "npm.log"
    environment = {
        **os.environ,
        "PATH": f"{tools}:{os.environ['PATH']}",
        "ROLLBACK_NPM_LOG": str(log),
    }

    completed = subprocess.run(
        [
            "bash",
            "scripts/javascript/rehearse-npm-rollback.sh",
            "HEAD",
            NPM_BASELINE,
        ],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "ci --ignore-scripts",
        "run test:web",
        "ci --prefix web --ignore-scripts",
        "run --prefix web bindings:check",
        "run --prefix web build",
        "run --prefix web test:production",
    ]
    assert "Rehearsed exact T67 candidate" in completed.stdout


@pytest.mark.unit
def test_rollback_contract_covers_every_migration_surface() -> None:
    rehearsal = Path("scripts/javascript/rehearse-npm-rollback.sh").read_text(
        encoding="utf-8"
    )
    for contract in (
        'rev-list --first-parent --reverse "$baseline..$candidate"',
        "unrelated first-parent commit in candidate range",
        "unrelated merge commit in candidate range",
        "baseline is not the direct npm parent of the T67 series",
        "rollback did not restore exact baseline bytes",
        "package-lock.json",
        "web/package-lock.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "docs/package-management.md",
        "scripts/javascript/bootstrap-pnpm.sh",
        "cache: npm",
        "COPY package.json package-lock.json ./",
        "npm run build && npm prune --omit=dev --ignore-scripts",
        "PUPPETEER_SKIP_DOWNLOAD=true npm ci --ignore-scripts",
        "npm ci --prefix spikes/toolchain --omit=dev --ignore-scripts",
        "grep -RIE '(pnpm|corepack)'",
    ):
        assert contract in rehearsal


@pytest.mark.unit
@pytest.mark.parametrize(
    ("candidate", "baseline", "failure"),
    [
        (NPM_BASELINE, NPM_BASELINE, "candidate does not contain T67"),
        ("HEAD", "HEAD^", "audited root npm lock"),
    ],
)
def test_rollback_rejects_unrelated_candidate_or_baseline_refs(
    tmp_path: Path, candidate: str, baseline: str, failure: str
) -> None:
    tools = _write_rollback_tool_stubs(tmp_path / "bin")
    completed = subprocess.run(
        [
            "bash",
            "scripts/javascript/rehearse-npm-rollback.sh",
            candidate,
            baseline,
        ],
        check=False,
        capture_output=True,
        env={
            **os.environ,
            "PATH": f"{tools}:{os.environ['PATH']}",
            "ROLLBACK_NPM_LOG": str(tmp_path / "npm.log"),
        },
        text=True,
    )

    assert completed.returncode != 0
    assert failure in completed.stderr
