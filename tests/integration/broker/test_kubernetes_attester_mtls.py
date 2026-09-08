from __future__ import annotations

import hashlib
import http.client
import ssl
import subprocess
import threading
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from markweave.broker.kubernetes_attester import NodeAttestationEngine
from markweave.broker.kubernetes_attester_inventory import SQLiteNodeAttesterLedger
from markweave.broker.kubernetes_attester_transport import (
    AttesterClientTlsConfig,
    AttesterHttpsServer,
    AttesterReadinessPolicy,
    AttesterServerTlsConfig,
    AttesterTransportLimits,
    HttpsNodeAttesterClient,
    NodeAttesterService,
)
from markweave.broker.kubernetes_runtime import (
    KubernetesAttestationContract,
    KubernetesRuntimeError,
    manifest_digest,
    pod_contract_projection,
)
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    RuntimeChannelLimits,
    RuntimeLimits,
    policy_specification_evidence,
)
from tests.unit.broker.test_kubernetes_runtime import (
    ATTEMPT_ID,
    IMAGE_DIGEST,
    PRINCIPAL_ID,
    UNIT_ID,
    _runtime,
)


def _run(*arguments: str) -> None:
    subprocess.run(
        arguments,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _certificates(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    ca = root / "ca.crt"
    ca_key = root / "ca.key"
    _run(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=t74-test-ca",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
        "-addext",
        "subjectKeyIdentifier=hash",
        "-keyout",
        str(ca_key),
        "-out",
        str(ca),
    )
    issued: list[Path] = []
    for name, eku, san in (
        ("server", "serverAuth", "DNS:localhost"),
        ("client", "clientAuth", "URI:spiffe://markweave/reverse-broker"),
    ):
        key = root / f"{name}.key"
        request = root / f"{name}.csr"
        certificate = root / f"{name}.crt"
        extensions = root / f"{name}.ext"
        extensions.write_text(
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature\n"
            f"extendedKeyUsage={eku}\n"
            f"subjectAltName={san}\n"
            "subjectKeyIdentifier=hash\n"
            "authorityKeyIdentifier=keyid,issuer\n",
            encoding="ascii",
        )
        _run(
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            f"/CN={name}",
            "-keyout",
            str(key),
            "-out",
            str(request),
        )
        _run(
            "openssl",
            "x509",
            "-req",
            "-days",
            "1",
            "-in",
            str(request),
            "-CA",
            str(ca),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-extfile",
            str(extensions),
            "-out",
            str(certificate),
        )
        issued.extend((certificate, key))
    if len(issued) != 4:
        raise RuntimeError("Test certificate set is incomplete")
    return ca, issued[0], issued[1], issued[2], issued[3]


def _digest(path: Path) -> EvidenceDigest:
    pem = path.read_text(encoding="ascii")
    der = ssl.PEM_cert_to_DER_cert(pem)
    return EvidenceDigest(f"sha256:{hashlib.sha256(der).hexdigest()}")


@pytest.fixture
def policy() -> BrokerPolicy:
    return BrokerPolicy(
        "t74-mtls",
        IMAGE_DIGEST,
        RuntimeLimits(100_000, 100_000, 268_435_456, 31, 16_777_216, 2_001),
        RuntimeChannelLimits(1_000_000, 2_000_000),
    )


@pytest.fixture
def unit(policy: BrokerPolicy) -> ManagedUnit:
    return ManagedUnit(
        ATTEMPT_ID,
        UNIT_ID,
        AuthenticatedPrincipal(PRINCIPAL_ID),
        1,
        policy.revision,
        policy_specification_evidence(policy),
        ManagedUnitState.CREATE_INTENT,
        1,
    )


@pytest.mark.integration
def test_mtls_attester_binds_exact_node_and_proof_chain(  # noqa: PLR0915
    tmp_path: Path,
    unit: ManagedUnit,
    policy: BrokerPolicy,
) -> None:
    runtime, control, inspector = _runtime(unit, policy)
    created = runtime.create(unit, policy)
    assert control.manifest is not None
    pod = replace(created.pod, node_name="localhost")
    observed = deepcopy(control.manifest)
    cast(dict[str, object], observed["metadata"])["uid"] = str(pod.pod_uid)
    cast(dict[str, object], observed["spec"])["nodeName"] = "localhost"
    inspector.override_sandbox = {"observed_pod": observed}
    contract_pod = pod_contract_projection(control.manifest)
    contract = KubernetesAttestationContract(
        contract_pod,
        manifest_digest(contract_pod),
        policy,
        "reverse",
        "fence-v1",
    )

    ca, server_certificate, server_key, client_certificate, client_key = _certificates(
        tmp_path
    )
    limits = AttesterTransportLimits(256 * 1024, 32 * 1024, 5.0, 8)
    readiness = AttesterReadinessPolicy(5.0, 0.01)
    service = NodeAttesterService(
        NodeAttestationEngine(inspector),
        SQLiteNodeAttesterLedger(
            tmp_path / "attester.sqlite3", b"a" * 32, max_records=8
        ),
        node_name="localhost",
    )
    server = AttesterHttpsServer(
        service,
        AttesterServerTlsConfig(
            "127.0.0.1",
            0,
            server_certificate,
            server_key,
            ca,
            _digest(client_certificate),
        ),
        limits,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = HttpsNodeAttesterClient(
        AttesterClientTlsConfig(
            server.address[1],
            client_certificate,
            client_key,
            ca,
            _digest(server_certificate),
        ),
        limits,
        readiness,
    )
    try:
        context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ca))
        context.load_cert_chain(str(client_certificate), str(client_key))

        def request_status(
            method: str,
            path: str,
            body: bytes,
            content_type: str = "application/json",
        ) -> int | str:
            connection = http.client.HTTPSConnection(
                "localhost", server.address[1], timeout=5, context=context
            )
            try:
                try:
                    connection.request(
                        method,
                        path,
                        body=body,
                        headers={"Content-Type": content_type},
                    )
                    return connection.getresponse().status
                except BrokenPipeError, ConnectionError:
                    return "connection-rejected"
            finally:
                connection.close()

        assert request_status("GET", "/v1/attest", b"") == 405
        assert request_status("POST", "/wrong", b"{}") == 400
        assert request_status("POST", "/v1/attest", b"{}", "text/plain") == 415
        assert request_status("POST", "/v1/attest", b"not-json") == 400
        assert request_status("POST", "/v1/attest", b"x" * (256 * 1024 + 1)) in {
            413,
            "connection-rejected",
        }

        wrong_pin = HttpsNodeAttesterClient(
            AttesterClientTlsConfig(
                server.address[1],
                client_certificate,
                client_key,
                ca,
                EvidenceDigest(f"sha256:{'0' * 64}"),
            ),
            limits,
            readiness,
        )
        with pytest.raises(KubernetesRuntimeError, match="server identity"):
            wrong_pin.bind(pod, contract)

        sandbox = client.bind(pod, contract)
        bound = replace(created, pod=pod, sandbox=sandbox)
        control.terminated = True
        exited = client.confirm_exit(bound)
        empty = client.confirm_empty(bound, exited)
        removed = client.confirm_removed(bound, empty)
        assert removed.value.startswith("sha256:")
        assert client.confirm_removed(bound, empty) == removed
    finally:
        server.shutdown()
        thread.join(timeout=5)
    assert not thread.is_alive()
