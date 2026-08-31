"""Real Compose rendering checks for the quickstart configuration."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from markweave.config import MalwareScanningMode, Settings

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def _load_rendered_settings(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]
) -> Settings:
    for field_name in Settings.model_fields:
        monkeypatch.delenv(f"MARKWEAVE_{field_name.upper()}", raising=False)
        monkeypatch.delenv(f"MD_CONVERTER_{field_name.upper()}", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    return Settings.load()


def test_trusted_upstream_podman_renders_custom_public_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the published port and strict login origin aligned."""
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "markweave-compose-contract",
            "--file",
            str(ROOT / "compose.yaml"),
            "--file",
            str(ROOT / "compose.simple.yaml"),
            "--file",
            str(ROOT / "compose.podman.yaml"),
            "--file",
            str(ROOT / "compose.trusted-upstream.yaml"),
            "--file",
            str(ROOT / "compose.podman-trusted-upstream.yaml"),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "MARKWEAVE_INITIAL_ADMIN_PASSWORD": "compose-contract-password",
            "MARKWEAVE_PORT": "11279",
            "MARKWEAVE_PUBLIC_ORIGIN": "https://converter.example",
            "MARKWEAVE_WORK_DEVICE": "/dev/null",
        },
    )
    document: dict[str, Any] = json.loads(result.stdout)
    application = document["services"]["markweave"]

    assert application["environment"]["MARKWEAVE_PUBLIC_ORIGIN"] == (
        "https://converter.example"
    )
    assert application["environment"]["MARKWEAVE_INSECURE_EVALUATION_MODE"] == "false"
    assert (
        application["environment"]["MARKWEAVE_MALWARE_SCANNING_MODE"]
        == "trusted-upstream"
    )
    assert not any(
        name.startswith("MD_CONVERTER_") for name in application["environment"]
    )
    assert (
        _load_rendered_settings(
            monkeypatch, application["environment"]
        ).malware_scanning_mode
        is MalwareScanningMode.TRUSTED_UPSTREAM
    )
    assert application["ports"] == [
        {
            "mode": "ingress",
            "host_ip": "127.0.0.1",
            "target": 8080,
            "published": "11279",
            "protocol": "tcp",
        }
    ]
    assert application["network_mode"] == "slirp4netns"


def test_clamav_free_compose_can_enable_insecure_evaluation_mode() -> None:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "markweave-insecure-compose-contract",
            "--file",
            str(ROOT / "compose.yaml"),
            "--file",
            str(ROOT / "compose.simple.yaml"),
            "--file",
            str(ROOT / "compose.trusted-upstream.yaml"),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "MARKWEAVE_INITIAL_ADMIN_PASSWORD": "compose-contract-password",
            "MARKWEAVE_INSECURE_EVALUATION_MODE": "true",
            "MARKWEAVE_WORK_DEVICE": "/dev/null",
        },
    )
    document: dict[str, Any] = json.loads(result.stdout)

    assert (
        document["services"]["markweave"]["environment"][
            "MARKWEAVE_INSECURE_EVALUATION_MODE"
        ]
        == "true"
    )
    assert not any(
        name.startswith("MD_CONVERTER_")
        for name in document["services"]["markweave"]["environment"]
    )
    assert "clamav" not in document["services"]["markweave"].get("depends_on", {})
