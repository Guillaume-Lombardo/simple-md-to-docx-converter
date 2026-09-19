"""Verify checkout metadata and explicit host installation without touching a node."""

from __future__ import annotations

import shlex
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
ASSETS = ("markweave-runc-wrapper", "markweave-isolated")
pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


@pytest.mark.parametrize("asset", ASSETS)
def test_node_asset_is_tracked_as_executable(asset: str) -> None:
    """Git records executability, not a portable checkout permission mask."""
    result = subprocess.run(
        ["git", "ls-files", "--stage", "--", f"deploy/k3s/{asset}"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.stdout.split()[0] == "100755"


def _install_command(asset: str, source: Path, destination: Path) -> list[str]:
    """Run the documented operator command, substituting only temporary paths."""
    commands = [
        shlex.split(line)
        for line in (ROOT / "docs/kubernetes-reverse-isolation.md")
        .read_text()
        .splitlines()
        if line.startswith("install --mode=")
    ]
    command = next(command for command in commands if command[3].endswith(asset))
    assert command[:3] == ["install", "--mode=0755", "--"]
    return [*command[:3], str(source), str(destination)]


@pytest.mark.parametrize("asset", ASSETS)
@pytest.mark.parametrize("source_mode", [0o755, 0o775, 0o777, 0o6755])
@pytest.mark.parametrize("umask", [0o002, 0o077])
def test_install_corrects_permissions_without_changing_source(
    tmp_path: Path, asset: str, source_mode: int, umask: int
) -> None:
    source = tmp_path / "checkout" / asset
    source.parent.mkdir()
    contents = (ROOT / "deploy/k3s" / asset).read_bytes()
    source.write_bytes(contents)
    source.chmod(source_mode)
    destination = tmp_path / "installed" / asset
    destination.parent.mkdir()
    destination.write_bytes(b"old installation")
    destination.chmod(0o777)

    subprocess.run(
        _install_command(asset, source, destination),
        check=True,
        capture_output=True,
        timeout=10,
        umask=umask,
    )

    assert destination.read_bytes() == contents
    assert stat.S_IMODE(destination.stat().st_mode) == 0o755
    assert source.read_bytes() == contents
    assert stat.S_IMODE(source.stat().st_mode) == source_mode


@pytest.mark.parametrize("asset", ASSETS)
def test_install_fails_for_missing_destination_parent(
    tmp_path: Path, asset: str
) -> None:
    source = tmp_path / asset
    source.write_bytes((ROOT / "deploy/k3s" / asset).read_bytes())
    source.chmod(0o775)
    destination = tmp_path / "missing" / asset

    result = subprocess.run(
        _install_command(asset, source, destination),
        check=False,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert not destination.parent.exists()
    assert stat.S_IMODE(source.stat().st_mode) == 0o775
