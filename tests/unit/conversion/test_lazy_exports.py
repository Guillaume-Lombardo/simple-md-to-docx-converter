"""Regression tests for the optional conversion-engine import boundary."""

from __future__ import annotations

import subprocess
import sys

import pytest

from markweave import conversion


@pytest.mark.unit
def test_conversion_package_does_not_eagerly_import_optional_engines() -> None:
    script = """
import sys
import markweave.conversion as conversion

for suffix in ("libreoffice", "mermaid", "pandoc", "service"):
    assert f"markweave.conversion.{suffix}" not in sys.modules
assert conversion.ImageLimits.__module__ == "markweave.conversion.images"
assert "markweave.conversion.images" in sys.modules
assert "markweave.conversion.pandoc" not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr


@pytest.mark.unit
def test_conversion_package_rejects_unknown_lazy_export() -> None:
    name = "unknown"
    with pytest.raises(AttributeError, match="has no attribute 'unknown'"):
        getattr(conversion, name)
