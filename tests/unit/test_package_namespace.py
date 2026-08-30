"""Tests for the retired-package namespace guard."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.check_package_namespace import (
    NamespaceError,
    check_namespace,
    cleanup_legacy_artifacts,
    legacy_cleanup_paths,
)

pytestmark = pytest.mark.unit


def _project(root: Path, *, version: str = "0.4.0") -> None:
    (root / "src" / "markweave").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "markweave"\nversion = "{version}"\n', encoding="utf-8"
    )
    (root / "src" / "markweave" / "version.py").write_text(
        f'VERSION = "{version}"\n', encoding="utf-8"
    )


def test_namespace_check_accepts_the_current_checkout() -> None:
    """The repository's public namespace and tracked tree are clean."""
    check_namespace(Path.cwd())


def test_namespace_check_rejects_retired_source_namespace(tmp_path: Path) -> None:
    """A residual package directory cannot silently reach editable imports."""
    _project(tmp_path)
    (tmp_path / "src" / "md_converter").mkdir()

    with pytest.raises(NamespaceError, match="unexpected source namespaces"):
        check_namespace(tmp_path)


def test_namespace_check_rejects_version_mismatch(tmp_path: Path) -> None:
    """Build artifacts cannot claim a version different from the source package."""
    _project(tmp_path, version="0.4.0")
    (tmp_path / "src" / "markweave" / "version.py").write_text(
        'VERSION = "9.9.9"\n', encoding="utf-8"
    )

    with pytest.raises(NamespaceError, match="does not match"):
        check_namespace(tmp_path)


def test_legacy_cleanup_removes_only_bytecode_and_obsolete_distributions(
    tmp_path: Path,
) -> None:
    """The documented cleanup is precise and leaves unrelated ignored files intact."""
    _project(tmp_path)
    cache = tmp_path / "src" / "md_converter" / "auth" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "service.cpython-314.pyc").write_bytes(b"bytecode")
    dist = tmp_path / "dist"
    dist.mkdir()
    retired_wheel = dist / "md_converter-0.1.0-py3-none-any.whl"
    retired_sdist = dist / "md_converter-0.1.0.tar.gz"
    retired_wheel.write_bytes(b"wheel")
    retired_sdist.write_bytes(b"sdist")
    preserved = dist / "markweave-0.4.0-py3-none-any.whl"
    preserved.write_bytes(b"current")

    planned = legacy_cleanup_paths(tmp_path)
    assert planned == (tmp_path / "src" / "md_converter", retired_wheel, retired_sdist)
    assert cleanup_legacy_artifacts(tmp_path, dry_run=True) == planned
    assert retired_wheel.exists()

    assert cleanup_legacy_artifacts(tmp_path, dry_run=False) == planned
    assert not (tmp_path / "src" / "md_converter").exists()
    assert not retired_wheel.exists()
    assert not retired_sdist.exists()
    assert preserved.exists()


def test_legacy_cleanup_refuses_untracked_source_code(tmp_path: Path) -> None:
    """Cleanup never deletes a legacy directory containing real source files."""
    _project(tmp_path)
    legacy = tmp_path / "src" / "md_converter"
    legacy.mkdir()
    (legacy / "app.py").write_text("pass\n", encoding="utf-8")

    with pytest.raises(NamespaceError, match="non-bytecode"):
        cleanup_legacy_artifacts(tmp_path, dry_run=False)
