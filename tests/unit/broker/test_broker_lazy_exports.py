"""Regression tests for the minimal broker import boundary."""

from __future__ import annotations

import subprocess
import sys

import pytest

from markweave import broker


@pytest.mark.unit
def test_broker_package_does_not_eagerly_import_runtime_or_transport_modules() -> None:
    script = """
import sys
import markweave.broker as broker

for suffix in ("mtls_transport", "podman_runtime", "unix_transport"):
    assert f"markweave.broker.{suffix}" not in sys.modules
assert broker.BrokerPolicy.__module__ == "markweave.broker.models"
assert "markweave.broker.models" in sys.modules
assert "markweave.broker.podman_runtime" not in sys.modules
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
def test_broker_package_rejects_unknown_lazy_export() -> None:
    name = "unknown"
    with pytest.raises(AttributeError, match="has no attribute 'unknown'"):
        getattr(broker, name)
