from __future__ import annotations

import hashlib
import json
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
