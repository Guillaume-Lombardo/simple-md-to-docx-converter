#!/usr/bin/env python3
"""Run one real T74 broker/attester/attempt lifecycle against k3s."""

from __future__ import annotations

import argparse
import hashlib
import ssl
import time
from pathlib import Path
from uuid import UUID, uuid4

from kubernetes import client

from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.kubernetes_api import (
    KubernetesApiConfig,
    KubernetesApiControlPlane,
)
from markweave.broker.kubernetes_attester_transport import (
    AttesterClientTlsConfig,
    AttesterReadinessPolicy,
    AttesterTransportLimits,
    HttpsNodeAttesterClient,
)
from markweave.broker.kubernetes_runtime import (
    KubernetesIsolationRuntime,
    KubernetesRuntimeConfig,
)
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    EvidenceDigest,
    ReplayPosition,
    RuntimeChannelLimits,
    RuntimeLimits,
)
from markweave.broker.service import IsolationBrokerService
from markweave.broker.workspace_protocol import (
    WorkspaceCollectRequest,
    WorkspacePendingResponse,
    WorkspaceStageRequest,
    WorkspaceSuccessResponse,
)
from markweave.reversions.models import ReverseContentLimits

_POLL_SECONDS = 0.25


def _certificate_digest(path: Path) -> EvidenceDigest:
    der = ssl.PEM_cert_to_DER_cert(path.read_text(encoding="ascii"))
    return EvidenceDigest(f"sha256:{hashlib.sha256(der).hexdigest()}")


def _api(*, token_file: Path, ca_file: Path) -> client.CoreV1Api:
    configuration = client.Configuration()
    configuration.host = "https://127.0.0.1:6443"
    configuration.ssl_ca_cert = str(ca_file)
    configuration.api_key["authorization"] = token_file.read_text(
        encoding="ascii"
    ).strip()
    configuration.api_key_prefix["authorization"] = "Bearer"
    return client.CoreV1Api(client.ApiClient(configuration))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-image-digest", required=True)
    parser.add_argument("--ca-file", type=Path, required=True)
    parser.add_argument("--client-certificate", type=Path, required=True)
    parser.add_argument("--client-key", type=Path, required=True)
    parser.add_argument("--cluster-ca-file", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--attempt-id", type=UUID, required=True)
    parser.add_argument("--principal-id", type=UUID, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--unit-id", type=UUID, required=True)
    arguments = parser.parse_args()

    principal = AuthenticatedPrincipal(arguments.principal_id)
    attempt_id = arguments.attempt_id
    channel_limits = RuntimeChannelLimits(1_000_000, 2_000_000)
    policy = BrokerPolicy(
        "t74-k3s-v1",
        arguments.attempt_image_digest,
        RuntimeLimits(50_000, 100_000, 134_217_728, 64, 33_554_432, 120_000),
        channel_limits,
    )
    control = KubernetesApiControlPlane(
        _api(token_file=arguments.token_file, ca_file=arguments.cluster_ca_file),
        KubernetesApiConfig("markweave-reverse", 60, 15, _POLL_SECONDS, channel_limits),
    )
    attester = HttpsNodeAttesterClient(
        AttesterClientTlsConfig(
            9443,
            arguments.client_certificate,
            arguments.client_key,
            arguments.ca_file,
            _certificate_digest(arguments.ca_file.parent / "server.crt"),
        ),
        AttesterTransportLimits(262_144, 131_072, 10, 4),
        AttesterReadinessPolicy(60, _POLL_SECONDS),
    )
    runtime = KubernetesIsolationRuntime(
        image_repository="localhost/markweave-reverse-attempt",
        policy=policy,
        config=KubernetesRuntimeConfig(
            "markweave-reverse",
            "markweave-reverse-attempt",
            "markweave-reverse",
            "reverse",
            "fence-v1",
            1001,
            1001,
            67_108_864,
        ),
        control_plane=control,
        node_attester=attester,
    )
    broker = IsolationBrokerService(
        SQLiteBrokerInventory(arguments.inventory, b"k" * 32, max_records=8),
        runtime,
        policy,
        max_discovered_units=8,
        unit_id_factory=lambda: arguments.unit_id,
    )
    broker.start()
    unit = broker.create(ReplayPosition(principal, 1), attempt_id)
    stage_request_id = uuid4()
    source = arguments.source.read_bytes()
    receipt = broker.stage_workspace(
        principal,
        WorkspaceStageRequest(
            stage_request_id,
            2,
            attempt_id,
            unit.unit_id,
            unit.create_sequence,
            arguments.source.suffix,
            ReverseContentLimits(
                1_000_000,
                2_000_000,
                500_000,
                4_096,
                4_096,
                16_777_216,
                10_000,
                64,
                64,
                1_000_000,
                1_500_000,
                1_000_000,
                2_000_000,
            ),
            source,
        ),
    )
    deadline = time.monotonic() + 90
    sequence = 3
    while True:
        response = broker.collect_workspace(
            principal,
            WorkspaceCollectRequest(
                uuid4(),
                sequence,
                receipt.request_id,
                receipt.stage_sequence,
                receipt.attempt_id,
                receipt.unit_id,
                receipt.create_sequence,
                receipt.incarnation_id,
            ),
        )
        sequence += 1
        if not isinstance(response, WorkspacePendingResponse):
            break
        if time.monotonic() >= deadline:
            raise RuntimeError("Kubernetes reverse conversion did not finish")
        time.sleep(_POLL_SECONDS)
    if not isinstance(response, WorkspaceSuccessResponse):
        raise RuntimeError("Kubernetes reverse conversion failed")
    proof = broker.terminate(principal, attempt_id, unit.unit_id)
    if not broker.acknowledge(principal, attempt_id, unit.unit_id, proof.proof_id):
        raise RuntimeError("Kubernetes proof acknowledgement failed")
    print(
        "Kubernetes reverse lifecycle passed: "
        f"result_bytes={len(response.result)} "
        f"result_sha256={hashlib.sha256(response.result).hexdigest()} "
        f"removal_evidence={proof.removal_evidence.value}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
