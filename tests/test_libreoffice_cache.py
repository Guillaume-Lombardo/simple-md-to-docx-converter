"""Contracts for checksum-locked LibreOffice archive caching."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
FETCHER = ROOT / "scripts/ci/prepare-libreoffice-archive.sh"
RPM_NAME = "LibreOffice_26.2.5_Linux_x86-64_rpm.tar.gz"
DEB_NAME = "LibreOffice_26.2.5_Linux_x86-64_deb.tar.gz"
RPM_SHA256 = "f62611c441ff1faa5cadb499abdbab119f5a9013eb6c0e32fc9aa65f6ff8b53d"
DEB_SHA256 = "2f03bfb2ac9f33ea7c77331b4b7a23300fb0ed7443566046bf8b5bc51c1bed1e"


def _fake_tools(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl_log = tmp_path / "curl.log"
    curl = fake_bin / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "output=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = --output ]; then shift; output=$1; fi\n'
        "  shift\n"
        "done\n"
        'test -n "$output"\n'
        'printf archive >"$output"\n'
        "printf 'download\\n' >>\"$FAKE_CURL_LOG\"\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)
    sha256sum = fake_bin / "sha256sum"
    sha256sum.write_text(
        '#!/bin/sh\nset -eu\ncat >/dev/null\nexit "${FAKE_SHA_EXIT:-0}"\n',
        encoding="utf-8",
    )
    sha256sum.chmod(0o755)
    return fake_bin, curl_log


@pytest.mark.parametrize(
    ("variant", "filename"), [("rpm", RPM_NAME), ("deb", DEB_NAME)]
)
def test_fetcher_downloads_once_and_reverifies_cache_hits(
    tmp_path: Path, variant: str, filename: str
) -> None:
    fake_bin, curl_log = _fake_tools(tmp_path)
    cache = tmp_path / "cache"
    environment = os.environ | {
        "FAKE_CURL_LOG": str(curl_log),
        "MARKWEAVE_TOOLCHAIN_CACHE_DIRECTORY": str(cache),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
    }

    for _ in range(2):
        subprocess.run([str(FETCHER), variant], check=True, env=environment)

    assert (cache / filename).read_bytes() == b"archive"
    assert curl_log.read_text(encoding="utf-8") == "download\n"


def test_fetcher_rejects_unknown_variants(tmp_path: Path) -> None:
    rejected = subprocess.run(
        [str(FETCHER), "zip"],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ
        | {"MARKWEAVE_TOOLCHAIN_CACHE_DIRECTORY": str(tmp_path / "cache")},
    )

    assert rejected.returncode != 0
    assert "LibreOffice archive variant must be rpm or deb" in rejected.stderr


def test_fetcher_rejects_a_corrupt_cache_hit_without_downloading(
    tmp_path: Path,
) -> None:
    fake_bin, curl_log = _fake_tools(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / RPM_NAME).write_bytes(b"corrupt")

    rejected = subprocess.run(
        [str(FETCHER), "rpm"],
        check=False,
        env=os.environ
        | {
            "FAKE_CURL_LOG": str(curl_log),
            "FAKE_SHA_EXIT": "1",
            "MARKWEAVE_TOOLCHAIN_CACHE_DIRECTORY": str(cache),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
        },
    )

    assert rejected.returncode != 0
    assert not curl_log.exists()


def test_fetcher_and_container_build_pin_the_reviewed_archives() -> None:
    fetcher = FETCHER.read_text(encoding="utf-8")
    containerfile = (ROOT / "Containerfile").read_text(encoding="utf-8")
    build = (ROOT / "scripts/container/build.sh").read_text(encoding="utf-8")

    for value in (RPM_NAME, DEB_NAME, RPM_SHA256, DEB_SHA256):
        assert value in fetcher
    assert "--retry 5 --retry-all-errors" in fetcher
    assert "sha256sum --check --strict" in fetcher
    assert "download.documentfoundation.org" not in containerfile
    assert "--mount=type=bind,from=libreoffice-archive" in containerfile
    assert "sha256sum --check --strict" in containerfile
    assert "prepare-libreoffice-archive.sh rpm" in build
    assert '--build-context "libreoffice-archive=$toolchain_cache_directory"' in build


def test_ci_uses_exact_restore_only_caches_and_trusted_saves() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["heavy"]["steps"]
    by_name = {step["name"]: step for step in steps}
    restore_pin = "actions/cache/restore@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"
    save_pin = "actions/cache/save@55cc8345863c7cc4c66a329aec7e433d2d1c52a9"

    for variant, digest in (("RPM", RPM_SHA256), ("DEB", DEB_SHA256)):
        restore = by_name[f"Restore verified LibreOffice {variant} archive"]
        save = by_name[f"Save verified LibreOffice {variant} archive"]
        assert restore["uses"] == restore_pin
        assert save["uses"] == save_pin
        assert digest in restore["with"]["key"]
        assert restore["with"]["key"] == save["with"]["key"]
        assert restore["with"]["path"] == save["with"]["path"]
        assert "restore-keys" not in restore["with"]
        assert "github.event_name == 'push'" in save["if"]
        assert "github.ref == 'refs/heads/main'" in save["if"]
        assert (
            "github.repository == 'Guillaume-Lombardo/simple-md-to-docx-converter'"
            in save["if"]
        )
