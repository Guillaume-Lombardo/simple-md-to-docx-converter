"""Persistent administrator approvals and encryption-key restore fencing."""

from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from ipaddress import ip_network
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from markweave.composer.admin_policy import ComposerAdminPolicy
from markweave.composer.connections import ConnectionConflictError
from markweave.composer.egress import (
    AllowedDestination,
    ConnectionPolicy,
    EgressPolicyError,
)
from markweave.composer.secrets import SecretCipher, SecretError
from markweave.config import Settings
from markweave.http.components import build_components
from markweave.persistence.composer.admin_policy import SqlComposerAdminPolicyRepository
from markweave.persistence.composer.connections import SqlConnectionRepository
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.schema import (
    ComposerAdminPolicyAuditRow,
    ComposerKeyIdentityRow,
)
from markweave.persistence.sql import create_database_engine
from tests.settings import template_settings

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def _ceiling(*, operator: bool = False) -> ConnectionPolicy:
    return ConnectionPolicy(
        destinations=(
            frozenset({AllowedDestination("models.example.test", 443)})
            if operator
            else frozenset()
        ),
        allowed_networks=(ip_network("192.0.2.0/24"),) if operator else (),
        maximum_request_bytes=4096,
        maximum_response_bytes=4096,
        maximum_models=8,
        maximum_model_name_length=128,
        maximum_credential_bytes=1024,
        maximum_output_tokens=128,
        maximum_concurrent_calls=2,
        maximum_allowed_users=8,
        timeout_seconds=5,
    )


def test_delegated_approval_is_disabled_until_exact_addresses_are_confirmed(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    try:
        repository = SqlComposerAdminPolicyRepository(engine)
        policy = ComposerAdminPolicy(
            repository,
            _ceiling(),
            delegated=True,
            resolver=lambda _host, _port: ("192.0.2.10",),
        )
        actor = uuid4()
        assert policy.read().enabled is False
        assert policy.snapshot().policy.destinations == frozenset()
        destination, addresses = policy.resolve("https://models.example.test/v1")
        assert destination == "models.example.test:443"
        assert addresses == ("192.0.2.10/32",)
        with pytest.raises(EgressPolicyError):
            policy.write(
                enabled=True,
                destinations=(destination,),
                networks=(),
                expected_version=0,
                actor_id=actor,
            )
        approved = policy.write(
            enabled=True,
            destinations=(destination,),
            networks=addresses,
            expected_version=0,
            actor_id=actor,
        )
        assert approved.version == 1
        assert policy.snapshot().enabled
        with pytest.raises(ConnectionConflictError):
            policy.write(
                enabled=False,
                destinations=(destination,),
                networks=addresses,
                expected_version=0,
                actor_id=actor,
            )
        with pytest.raises(EgressPolicyError):
            policy.write(
                enabled=True,
                destinations=(destination,),
                networks=("0.0.0.0/0",),
                expected_version=1,
                actor_id=actor,
            )
        assert (
            ComposerAdminPolicy(repository, _ceiling(), delegated=True).read()
            == approved
        )
        disabled = policy.write(
            enabled=False,
            destinations=(destination,),
            networks=addresses,
            expected_version=1,
            actor_id=actor,
        )
        assert disabled.version == 2
        assert policy.snapshot().enabled is False
        with Session(engine) as database:
            assert database.query(ComposerAdminPolicyAuditRow).count() == 2
    finally:
        engine.dispose()


def test_operator_ceiling_cannot_be_expanded_and_existing_default_remains_enabled(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    try:
        policy = ComposerAdminPolicy(
            SqlComposerAdminPolicyRepository(engine),
            _ceiling(operator=True),
            delegated=False,
        )
        assert policy.read().enabled
        assert policy.read().destinations == ("models.example.test:443",)
        with pytest.raises(EgressPolicyError):
            policy.write(
                enabled=True,
                destinations=("another.example.test:443",),
                networks=("192.0.2.10/32",),
                expected_version=0,
                actor_id=uuid4(),
            )
        narrowed = policy.write(
            enabled=True,
            destinations=("models.example.test:443",),
            networks=("192.0.2.10/32",),
            expected_version=0,
            actor_id=uuid4(),
        )
        assert narrowed.version == 1
        assert str(policy.snapshot().policy.allowed_networks[0]) == "192.0.2.10/32"
    finally:
        engine.dispose()


def test_operator_ceiling_replacement_requires_explicit_reapproval(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    try:
        repository = SqlComposerAdminPolicyRepository(engine)
        old = ComposerAdminPolicy(repository, _ceiling(operator=True), delegated=False)
        first = old.write(
            enabled=True,
            destinations=("models.example.test:443",),
            networks=("192.0.2.10/32",),
            expected_version=0,
            actor_id=uuid4(),
        )
        new_ceiling = _ceiling(operator=True)
        new_ceiling = replace(
            new_ceiling,
            destinations=frozenset({AllowedDestination("new.example.test", 8443)}),
            allowed_networks=(ip_network("198.51.100.0/24"),),
        )
        replaced = ComposerAdminPolicy(repository, new_ceiling, delegated=False)
        effective = replaced.read()
        assert effective.version == first.version
        assert not effective.enabled
        assert effective.destinations == ("new.example.test:8443",)
        assert effective.networks == ("198.51.100.0/24",)
        assert not replaced.snapshot().enabled
        renewed = replaced.write(
            enabled=True,
            destinations=effective.destinations,
            networks=effective.networks,
            expected_version=effective.version,
            actor_id=uuid4(),
        )
        assert renewed.enabled and renewed.version == first.version + 1
        assert replaced.snapshot().enabled
    finally:
        engine.dispose()


def test_database_key_identity_rejects_a_wrong_key_before_writes(
    tmp_path: Path,
) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    try:
        repository = SqlConnectionRepository(engine, maximum_allowed_users=8)
        first = SecretCipher(b"a" * 32)
        wrong = SecretCipher(b"b" * 32)
        repository.bind_key_identity(first)
        with Session(engine) as database:
            identity = database.get(ComposerKeyIdentityRow, 1)
            assert identity is not None
            assert identity.fingerprint == first.fingerprint
        repository.bind_key_identity(first)
        with pytest.raises(SecretError):
            repository.bind_key_identity(wrong)
    finally:
        engine.dispose()


def test_wrong_or_missing_key_keeps_non_model_components_available(
    tmp_path: Path,
) -> None:
    key = tmp_path / "composer-key"
    original = b"a" * 32
    key.write_text(original.hex(), encoding="ascii")
    key.chmod(0o600)
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password="admin-password",  # noqa: S106 - test-only credential
        storage_profile="standalone",
        standalone_data_directory=tmp_path / "data",
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3600,
        composer_enabled=True,
        composer_admin_policy_delegated=True,
        composer_allowed_destinations=[],
        composer_allowed_networks=[],
        composer_secret_key_path=key,
        composer_upload_max_bytes=8192,
        composer_http_request_max_bytes=9216,
        composer_maximum_request_bytes=4096,
        composer_maximum_response_bytes=4096,
        composer_maximum_models=8,
        composer_maximum_allowed_users=100,
        composer_maximum_model_name_length=128,
        composer_maximum_credential_bytes=8192,
        composer_maximum_output_tokens=128,
        composer_maximum_concurrent_calls=2,
        composer_retry_after_seconds=3,
        composer_timeout_seconds=5.0,
        composer_pending_publication_stale_seconds=30.0,
        composer_draft_retention_seconds=3600,
    )
    with closing(build_components(settings)) as components:
        assert components.composer_connections is not None
        assert components.composer_admin_policy is not None
    for value in (None, (b"b" * 32).hex()):
        if value is None:
            key.unlink()
        else:
            key.write_text(value, encoding="ascii")
        with closing(build_components(settings)) as components:
            assert components.composer_connections is None
            assert components.composer_admin_policy is None
            assert components.jobs is not None
            assert components.composer_store is not None
    key.write_text(original.hex(), encoding="ascii")
    key.chmod(0o600)
    with closing(build_components(settings)) as components:
        assert components.composer_connections is not None
        assert components.composer_admin_policy is not None


def test_admin_policy_audit_requires_guarded_retention_cleanup(tmp_path: Path) -> None:
    engine = create_database_engine(
        f"sqlite+pysqlite:///{tmp_path / 'metadata.sqlite3'}"
    )
    upgrade_database(engine)
    try:
        row_id = str(uuid4())
        with Session(engine) as database, database.begin():
            database.add(
                ComposerAdminPolicyAuditRow(
                    id=row_id,
                    actor_id=str(uuid4()),
                    old_enabled=False,
                    new_enabled=True,
                    version=1,
                    created_at=datetime.now(UTC) - timedelta(days=40),
                )
            )
        with (
            pytest.raises(IntegrityError),
            Session(engine) as database,
            database.begin(),
        ):
            database.execute(
                delete(ComposerAdminPolicyAuditRow).where(
                    ComposerAdminPolicyAuditRow.id == row_id
                )
            )
        repository = SqlConnectionRepository(engine, maximum_allowed_users=8)
        assert (
            repository.cleanup_connection_audit(
                cutoff_at=datetime.now(UTC) - timedelta(days=30), limit=1
            )
            == 1
        )
        with Session(engine) as database:
            assert database.get(ComposerAdminPolicyAuditRow, row_id) is None
    finally:
        engine.dispose()
