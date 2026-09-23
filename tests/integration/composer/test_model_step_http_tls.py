"""Real HTTP, SQL, and private HTTPS cancellation preserves pending review state."""

import ssl
from collections.abc import Iterator
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_network
from pathlib import Path
from threading import Event, Thread
from typing import cast
from uuid import UUID, uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture
from sqlalchemy import select
from sqlalchemy.orm import Session

from markweave.app import AppComponents, create_app
from markweave.auth.memory import MemoryReadinessProbe
from markweave.auth.models import Role, User
from markweave.auth.service import AuthenticationService
from markweave.composer.connections import (
    AllowedDestination,
    ConnectionActor,
    ConnectionPolicy,
    ConnectionService,
    ConnectionState,
)
from markweave.composer.egress import ConnectionEgress
from markweave.composer.secrets import SecretCipher
from markweave.config import Settings
from markweave.http.composer_step_runner import ComposerStepRunner
from markweave.persistence.composer import (
    SqlComposerModelStepRepository,
    SqlComposerRepository,
    SqlConnectionRepository,
)
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import (
    ComposerConnectionGrantRow,
    ComposerConnectionRow,
    ComposerCredentialRow,
    ComposerProposalRow,
    UserRow,
)
from markweave.persistence.sql import create_database_engine
from markweave.storage import FilesystemObjectStore
from tests.settings import template_settings

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


class _Provider(ThreadingHTTPServer):
    entered: Event
    release: Event
    delayed: bool


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        provider = cast(_Provider, self.server)
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        provider.entered.set()
        if provider.delayed and not provider.release.wait(timeout=2):
            return
        body = b'{"choices":[{"message":{"content":"Pending review"}}]}'
        with suppress(OSError):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _certificate(tmp_path: Path) -> tuple[Path, Path, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "provider.crt", tmp_path / "provider.key"
    cert = certificate.public_bytes(serialization.Encoding.PEM)
    cert_path.write_bytes(cert)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path, cert


@pytest.fixture
def provider(tmp_path: Path) -> Iterator[tuple[_Provider, bytes]]:
    cert_path, key_path, cert = _certificate(tmp_path)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server = _Provider(("127.0.0.1", 0), _Handler)
    server.entered, server.release, server.delayed = Event(), Event(), True
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, cert
    finally:
        server.release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _wait_status(client: TestClient, draft_id: UUID, step_id: UUID, state: str) -> dict:
    for _attempt in range(40):
        response = client.get(
            f"/api/v1/composer/drafts/{draft_id}/model-steps/{step_id}"
        )
        assert response.status_code == 200
        if response.json()["status"] == state:
            return response.json()
        Event().wait(0.05)
    pytest.fail(f"Composer model step did not become {state}")


def test_http_cancel_reaches_sql_and_private_tls_without_late_proposal(  # noqa: PLR0915 - complete boundary lifecycle
    tmp_path: Path,
    provider: tuple[_Provider, bytes],
    mocker: MockerFixture,
) -> None:
    server, cert = provider
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    owner_id, connection_id = uuid4(), uuid4()
    endpoint = f"https://localhost:{server.server_port}/v1"
    cipher = SecretCipher(b"s" * 32)
    with Session(engine) as database, database.begin():
        database.add(
            UserRow(
                id=str(owner_id),
                username="owner",
                normalized_username="owner",
                password_hash="hash",  # noqa: S106 - isolated fixture
                role="user",
                active=True,
                auth_version=0,
                password_change_required=False,
            )
        )
        database.flush()
        database.add(
            ComposerConnectionRow(
                id=str(connection_id),
                scope="instance",
                owner_id=None,
                identity_mode="shared",
                name="Private TLS",
                endpoint=endpoint,
                selected_model="model-1",
                permitted_models='["model-1"]',
                enabled=True,
                version=1,
                generation=1,
                outage=False,
            )
        )
        database.flush()
        database.add(
            ComposerConnectionGrantRow(
                connection_id=str(connection_id), user_id=str(owner_id)
            )
        )
        database.add(
            ComposerCredentialRow(
                id=str(uuid4()),
                connection_id=str(connection_id),
                user_id=None,
                api_key=cipher.seal(connection_id, "api_key", b"private-key"),
                client_certificate=None,
                client_private_key=None,
                ca_bundle=cipher.seal(connection_id, "ca_bundle", cert),
                outage=False,
            )
        )
    objects = FilesystemObjectStore(tmp_path)
    drafts = SqlComposerRepository(engine, objects)
    draft = drafts.create_draft_with_source(
        owner_id,
        b"source",
        "scanner-approved",
        title="Draft",
        content="original",
        media_type="text/markdown",
    )
    policy = ConnectionPolicy(
        destinations=frozenset({AllowedDestination("localhost", server.server_port)}),
        allowed_networks=(ip_network("127.0.0.0/8"),),
        maximum_request_bytes=1024,
        maximum_response_bytes=1024,
        maximum_models=10,
        maximum_model_name_length=100,
        maximum_credential_bytes=4096,
        maximum_output_tokens=8,
        maximum_concurrent_calls=1,
        maximum_allowed_users=10,
        timeout_seconds=3,
    )
    connections = SqlConnectionRepository(engine, maximum_allowed_users=10)
    service = ConnectionService(
        connections,
        cipher,
        ConnectionEgress(policy, lambda _host, _port: ("127.0.0.1",)),
        policy,
    )
    steps = SqlComposerModelStepRepository(engine)
    runner = ComposerStepRunner(
        steps, service, maximum_active=1, lease=timedelta(seconds=4)
    )
    owner = User(owner_id, "owner", "owner", "hash", Role.USER)
    authentication = mocker.Mock(spec=AuthenticationService)
    authentication.bootstrap_admin.return_value = owner
    authentication.authenticate.return_value = owner
    settings = Settings(
        **template_settings(),
        initial_admin_username="owner",
        initial_admin_password="owner-" + "password",
        storage_profile="standalone",
        standalone_data_directory=str(tmp_path),
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3600,
        composer_http_request_max_bytes=4096,
        composer_maximum_request_bytes=1024,
        composer_maximum_output_tokens=8,
    )
    app = create_app(
        settings,
        components=AppComponents(
            authentication=authentication,
            readiness=MemoryReadinessProbe(),
            object_store=objects,
            jobs=mocker.Mock(),
            composer_store=drafts,
            composer_connection_repository=connections,
            composer_connections=service,
            composer_model_step_repository=steps,
            composer_steps=runner,
        ),
    )
    client = TestClient(app, base_url="https://testserver")
    path = f"/api/v1/composer/drafts/{draft.id}/model-steps"
    body = {
        "connection_id": str(connection_id),
        "approved_endpoint": endpoint,
        "approved_model": "model-1",
        "content": "Explicitly approved text",
        "max_output_tokens": 2,
    }
    try:
        with client:
            started = client.post(
                path,
                json=body,
                headers={
                    "If-Match": draft.etag,
                    "Idempotency-Key": "cancelled-call",
                    "X-CSRF-Token": "csrf",
                },
            )
            assert started.status_code == 202, started.text
            step_id = UUID(started.json()["id"])
            assert server.entered.wait(2)
            cancelled = client.delete(
                f"{path}/{step_id}", headers={"X-CSRF-Token": "csrf"}
            )
            assert cancelled.status_code == 200
            server.release.set()
            result = _wait_status(client, draft.id, step_id, "cancelled")
            assert result["proposal_id"] is None
            with Session(engine) as database:
                assert database.scalars(select(ComposerProposalRow)).all() == []
            server.delayed = False
            retry = client.post(
                path,
                json=body,
                headers={
                    "If-Match": draft.etag,
                    "Idempotency-Key": "next-call",
                    "X-CSRF-Token": "csrf",
                },
            )
            assert retry.status_code == 202, retry.text
            completed = _wait_status(
                client, draft.id, UUID(retry.json()["id"]), "completed"
            )
            assert completed["proposal_id"] is not None
            assert (
                _wait_status(client, draft.id, step_id, "cancelled")["proposal_id"]
                is None
            )
            with Session(engine) as database:
                proposals = database.scalars(select(ComposerProposalRow)).all()
                assert len(proposals) == 1
                assert proposals[0].id == completed["proposal_id"]

            server.delayed = True
            server.release.clear()
            server.entered.clear()
            actor = ConnectionActor(owner_id, is_admin=False, can_manage_personal=False)
            test_errors: list[Exception] = []

            def occupying_connection_test() -> None:
                try:
                    service.test(actor, connection_id, model="model-1")
                except Exception as error:
                    test_errors.append(error)

            occupying = Thread(target=occupying_connection_test)
            occupying.start()
            try:
                assert server.entered.wait(2)
                admitted = client.post(
                    path,
                    json=body,
                    headers={
                        "If-Match": drafts.get_draft(owner_id, draft.id).etag,
                        "Idempotency-Key": "capacity-after-admission",
                        "X-CSRF-Token": "csrf",
                    },
                )
                assert admitted.status_code == 202, admitted.text
                exhausted = _wait_status(
                    client, draft.id, UUID(admitted.json()["id"]), "failed"
                )
                assert exhausted["error_code"] == "capacity_exhausted"
                assert exhausted["proposal_id"] is None
                assert service.availability(actor).state is ConnectionState.READY
                with Session(engine) as database:
                    assert len(database.scalars(select(ComposerProposalRow)).all()) == 1
            finally:
                server.release.set()
                occupying.join(timeout=4)
            assert not occupying.is_alive()
            assert test_errors == []
    finally:
        runner.close()
        engine.dispose()
