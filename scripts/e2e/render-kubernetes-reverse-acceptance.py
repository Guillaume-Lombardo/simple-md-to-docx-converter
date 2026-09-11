#!/usr/bin/env python3
"""Render the T74 reference deployment for a disposable acceptance run."""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
from pathlib import Path

_DIGEST_CHARACTERS = 71


def _sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _certificate_sha256(path: Path) -> str:
    result = subprocess.run(  # noqa: S603 - fixed local acceptance dependency
        ("/usr/bin/openssl", "x509", "-in", str(path), "-outform", "DER"),
        check=True,
        capture_output=True,
    )
    return f"sha256:{hashlib.sha256(result.stdout).hexdigest()}"


def _yaml_block(path: Path) -> str:
    value = path.read_text(encoding="ascii").rstrip()
    return "|\n    " + value.replace("\n", "\n    ")


def _digest(value: str) -> str:
    if not value.startswith("sha256:") or len(value) != _DIGEST_CHARACTERS:
        raise ValueError("image digest must be a complete SHA-256 digest")
    int(value.removeprefix("sha256:"), 16)
    return value


def _write_private_text(path: Path, value: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(value)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def render(
    *,
    repository: Path,
    credentials: Path,
    output: Path,
    attester_digest: str,
    proxy_digest: str,
) -> None:
    """Render all placeholders without emitting private material."""

    template = repository / "deploy/reverse-kubernetes.yaml.example"
    text = template.read_text(encoding="utf-8")
    replacements = {
        '"@REQUIRED_ATTESTER_SERVER_CA_PEM@"': _yaml_block(credentials / "ca.crt"),
        '"@REQUIRED_BROKER_CLIENT_CERTIFICATE_PEM@"': _yaml_block(
            credentials / "client.crt"
        ),
        '"@REQUIRED_BROKER_CLIENT_PRIVATE_KEY_PEM@"': _yaml_block(
            credentials / "client.key"
        ),
        '"@REQUIRED_BROKER_CLIENT_CA_PEM@"': _yaml_block(credentials / "ca.crt"),
        '"@REQUIRED_ATTESTER_CERTIFICATE_PEM@"': _yaml_block(
            credentials / "server.crt"
        ),
        '"@REQUIRED_ATTESTER_PRIVATE_KEY_PEM@"': _yaml_block(
            credentials / "server.key"
        ),
        '"@REQUIRED_ATTESTER_INVENTORY_AUTHENTICATION_KEY@"': (
            credentials / "inventory-key"
        )
        .read_text(encoding="ascii")
        .strip(),
        "@REQUIRED_ATTESTER_SERVER_CERTIFICATE_SHA256@": _certificate_sha256(
            credentials / "server.crt"
        ),
        "@REQUIRED_BROKER_CLIENT_CERTIFICATE_SHA256@": _certificate_sha256(
            credentials / "client.crt"
        ),
        "@REQUIRED_NODE_FENCE_REVISION@": "fence-v1",
        "@REQUIRED_CNI_CONFIG_SHA256@": _sha256(
            repository / "deploy/k3s/00-markweave-isolated.conflist"
        ),
        "@REQUIRED_CNI_PLUGIN_SHA256@": _sha256(
            repository / "deploy/k3s/markweave-isolated"
        ),
        "@REQUIRED_RUNTIME_CONFIG_SHA256@": _sha256(
            repository / "deploy/k3s/20-markweave-reverse-runtime.toml"
        ),
        "@REQUIRED_RUNTIME_WRAPPER_SHA256@": _sha256(
            repository / "deploy/k3s/markweave-runc-wrapper"
        ),
        "@REQUIRED_ATTESTER_IMAGE_REPOSITORY@": (
            "localhost/markweave-kubernetes-attester"
        ),
        "@REQUIRED_ATTESTER_IMAGE_DIGEST@": _digest(attester_digest),
        "@REQUIRED_CRI_PROXY_IMAGE_REPOSITORY@": "docker.io/envoyproxy/envoy",
        "@REQUIRED_CRI_PROXY_IMAGE_DIGEST@": _digest(proxy_digest),
        "@REQUIRED_POD_SCHEDULING_TIMEOUT_SECONDS@": "60",
        "@REQUIRED_POD_EXEC_TIMEOUT_SECONDS@": "15",
        "@REQUIRED_POD_POLL_INTERVAL_SECONDS@": "0.25",
        "@REQUIRED_ATTESTER_MAX_REQUEST_BYTES@": "262144",
        "@REQUIRED_ATTESTER_MAX_RESPONSE_BYTES@": "131072",
        "@REQUIRED_ATTESTER_REQUEST_TIMEOUT_SECONDS@": "10",
        "@REQUIRED_ATTESTER_READINESS_TIMEOUT_SECONDS@": "60",
        "@REQUIRED_ATTESTER_READINESS_POLL_INTERVAL_SECONDS@": "0.25",
        "@REQUIRED_ATTESTER_OPERATION_TIMEOUT_SECONDS@": "5",
        "@REQUIRED_ATTESTER_HARD_SHUTDOWN_TIMEOUT_SECONDS@": "10",
        "@REQUIRED_ATTESTER_TERMINATION_GRACE_PERIOD_SECONDS@": "15",
        "@REQUIRED_ATTESTER_MAX_RECORDS@": "32",
        "@REQUIRED_ATTESTER_MAX_CONCURRENT_REQUESTS@": "4",
        "@REQUIRED_ATTESTER_MAX_SANDBOXES@": "128",
        "@REQUIRED_ATTESTER_MAX_CONTAINERS@": "256",
        "@REQUIRED_ATTESTER_MAX_DESCENDANT_PIDS@": "64",
    }
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    if "@REQUIRED_" in text:
        raise ValueError("deployment contains an unrendered placeholder")
    _write_private_text(output, text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attester-digest", required=True)
    parser.add_argument("--proxy-digest", required=True)
    arguments = parser.parse_args()
    render(
        repository=arguments.repository.resolve(),
        credentials=arguments.credentials.resolve(),
        output=arguments.output.resolve(),
        attester_digest=arguments.attester_digest,
        proxy_digest=arguments.proxy_digest,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
