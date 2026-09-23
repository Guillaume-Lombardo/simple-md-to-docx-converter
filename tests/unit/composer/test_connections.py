"""Connection authorization, encrypted credentials, and stale-call fencing."""

from __future__ import annotations

import time
from dataclasses import replace
from ipaddress import ip_network
from threading import Event
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture

from markweave.composer.connections import (
    AllowedDestination,
    ConnectionActor,
    ConnectionAuthorizationError,
    ConnectionAvailability,
    ConnectionConfigurationError,
    ConnectionConflictError,
    ConnectionGateway,
    ConnectionNotFoundError,
    ConnectionPolicy,
    ConnectionRecord,
    ConnectionScope,
    ConnectionService,
    ConnectionState,
    EncryptedCredentials,
    IdentityMode,
    PlainCredentials,
    SecretCipher,
)
from markweave.composer.egress import (
    ConnectionEgress,
    EgressCancelledError,
    EgressCapacityError,
    EgressPolicyError,
    EgressResponseError,
    EgressUnavailableError,
    validate_endpoint,
)
from markweave.composer.secrets import SecretError

pytestmark = pytest.mark.unit


class MemoryConnections:
    def __init__(self) -> None:
        self.records: dict[UUID, ConnectionRecord] = {}
        self.credentials: dict[tuple[UUID, UUID | None], EncryptedCredentials] = {}
        self.personal_permissions: set[UUID] = set()
        self.outages: set[tuple[UUID, UUID | None]] = set()

    def get_connection(self, connection_id: UUID) -> ConnectionRecord | None:
        return self.records.get(connection_id)

    def list_connections(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[ConnectionRecord, ...]:
        ordered = sorted(self.records.values(), key=lambda record: str(record.id))
        return tuple(ordered[offset : offset + limit])

    def list_visible_connections(
        self,
        user_id: UUID,
        is_admin: bool,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[ConnectionRecord, ...]:
        visible = sorted(
            (
                record
                for record in self.records.values()
                if (
                    record.scope is ConnectionScope.PERSONAL
                    and record.owner_id == user_id
                )
                or (
                    record.scope is ConnectionScope.INSTANCE
                    and (is_admin or user_id in record.allowed_user_ids)
                )
            ),
            key=lambda record: str(record.id),
        )
        return tuple(visible[offset : offset + limit])

    def get_credentials(
        self, connection_id: UUID, user_id: UUID | None
    ) -> EncryptedCredentials | None:
        return self.credentials.get((connection_id, user_id))

    def can_manage_personal(self, user_id: UUID) -> bool:
        return user_id in self.personal_permissions

    def get_outage(self, connection_id: UUID, user_id: UUID | None) -> bool:
        return (connection_id, user_id) in self.outages

    def set_outage(
        self,
        connection_id: UUID,
        user_id: UUID | None,
        outage: bool,
        *,
        expected_generation: int,
    ) -> bool:
        current = self.records.get(connection_id)
        if current is None or current.generation != expected_generation:
            return False
        if outage:
            self.outages.add((connection_id, user_id))
        else:
            self.outages.discard((connection_id, user_id))
        return True

    def save_connection(
        self,
        record: ConnectionRecord,
        *,
        expected_version: int | None,
        actor_id: UUID,
        credential_user_id: UUID | None = None,
        credentials: EncryptedCredentials | None = None,
    ) -> ConnectionRecord:
        previous = self.records.get(record.id)
        if (previous.version if previous else None) != expected_version:
            raise ConnectionConflictError("Connection changed")
        saved = replace(
            record,
            version=(previous.version + 1 if previous else 1),
            generation=(previous.generation + 1 if previous else 1),
        )
        self.records[record.id] = saved
        self.outages = {key for key in self.outages if key[0] != record.id}
        if credentials is not None:
            self.credentials[(record.id, credential_user_id)] = credentials
        return saved

    def revoke_connection(
        self,
        record: ConnectionRecord,
        *,
        expected_version: int,
        actor_id: UUID,
    ) -> ConnectionRecord:
        current = self.records.get(record.id)
        if current is None or current.version != expected_version:
            raise ConnectionConflictError("Connection changed")
        revoked = replace(
            current,
            enabled=False,
            allowed_user_ids=frozenset(),
            has_api_key=False,
            has_client_certificate=False,
            has_ca_bundle=False,
            version=current.version + 1,
            generation=current.generation + 1,
        )
        self.records[record.id] = revoked
        self.credentials = {
            key: value for key, value in self.credentials.items() if key[0] != record.id
        }
        self.outages = {key for key in self.outages if key[0] != record.id}
        return revoked


class FakeEgress:
    def __init__(self) -> None:
        self.before_return: object | None = None
        self.calls = 0

    def discover_models(
        self,
        endpoint_url: str,
        credentials: PlainCredentials,
        *,
        cancel_event: Event | None = None,
    ) -> tuple[str, ...]:
        self.calls += 1
        assert credentials.api_key == b"private"
        if callable(self.before_return):
            self.before_return()
        return ("permitted", "other")

    def chat(  # noqa: PLR0913 - matches cancellable gateway contract
        self,
        endpoint_url: str,
        credentials: PlainCredentials,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        cancel_event: Event | None = None,
    ) -> dict[str, object]:
        self.calls += 1
        assert credentials.api_key == b"private"
        assert model == "permitted"
        if callable(self.before_return):
            self.before_return()
        return {"choices": [{"message": {"content": "OK"}}]}


@pytest.fixture
def policy() -> ConnectionPolicy:
    return ConnectionPolicy(
        destinations=frozenset({AllowedDestination("models.example", 443)}),
        allowed_networks=(ip_network("203.0.113.0/24"),),
        maximum_request_bytes=4096,
        maximum_response_bytes=4096,
        maximum_models=10,
        maximum_model_name_length=100,
        maximum_credential_bytes=4096,
        maximum_output_tokens=32,
        maximum_concurrent_calls=2,
        maximum_allowed_users=10,
        timeout_seconds=2,
    )


@pytest.fixture
def actors() -> tuple[ConnectionActor, ConnectionActor, ConnectionActor]:
    return (
        ConnectionActor(uuid4(), True, True),
        ConnectionActor(uuid4(), False, True),
        ConnectionActor(uuid4(), False, False),
    )


def _record(owner: UUID | None, allowed: frozenset[UUID]) -> ConnectionRecord:
    return ConnectionRecord(
        id=uuid4(),
        scope=ConnectionScope.PERSONAL if owner else ConnectionScope.INSTANCE,
        owner_id=owner,
        identity_mode=IdentityMode.INDIVIDUAL if owner else IdentityMode.SHARED,
        endpoint="https://models.example/v1",
        selected_model="permitted",
        permitted_models=("permitted",),
        enabled=True,
        allowed_user_ids=allowed,
        version=0,
        generation=0,
        name="Primary model",
    )


def _service(
    repository: MemoryConnections, policy: ConnectionPolicy, egress: ConnectionGateway
) -> ConnectionService:
    return ConnectionService(repository, SecretCipher(b"s" * 32), egress, policy)


def test_connection_grant_limit_rejects_create_and_update_without_writing(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, _, _ = actors
    repository = MemoryConnections()
    service = _service(
        repository, replace(policy, maximum_allowed_users=2), FakeEgress()
    )
    grants = frozenset({UUID(int=1), UUID(int=2), UUID(int=3)})
    oversized = _record(None, grants)

    with pytest.raises(ConnectionConfigurationError, match="access list exceeds"):
        service.save(admin, oversized, expected_version=None)
    assert repository.records == {}

    saved = service.save(
        admin,
        replace(oversized, allowed_user_ids=frozenset({UUID(int=1), UUID(int=2)})),
        expected_version=None,
    )
    with pytest.raises(ConnectionConfigurationError, match="access list exceeds"):
        service.save(
            admin,
            replace(saved, allowed_user_ids=grants),
            expected_version=saved.version,
        )
    assert repository.records[saved.id] == saved
    assert len(service.list_visible(admin, limit=1)[0].allowed_user_ids) == 2


@pytest.mark.parametrize("limit", [0, -1, True])
def test_connection_grant_policy_rejects_invalid_limits(
    policy: ConnectionPolicy, limit: int
) -> None:
    with pytest.raises(ValueError, match=r"Composer .*limit"):
        replace(policy, maximum_allowed_users=limit)


def test_configured_grant_limit_above_one_thousand_is_respected(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, _, _ = actors
    repository = MemoryConnections()
    service = _service(
        repository, replace(policy, maximum_allowed_users=1001), FakeEgress()
    )
    grants = frozenset(UUID(int=index) for index in range(1, 1002))
    record = _record(None, grants)
    saved = service.save(admin, record, expected_version=None)
    assert len(saved.allowed_user_ids) == 1001

    with pytest.raises(ConnectionConfigurationError, match="access list exceeds"):
        service.save(
            admin,
            replace(saved, allowed_user_ids=grants | {UUID(int=1002)}),
            expected_version=saved.version,
        )
    assert repository.records[saved.id] == saved


def test_personal_connection_is_owner_only_and_write_only(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, owner, other = actors
    repository = MemoryConnections()
    repository.personal_permissions.add(owner.id)
    egress = FakeEgress()
    service = _service(repository, policy, egress)
    record = service.save(
        owner,
        _record(owner.id, frozenset()),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
        credential_user_id=owner.id,
    )
    assert b"private" not in repr(record).encode()
    assert b"private" not in repr(repository.records).encode()
    assert service.credential_presence(owner, record.id).has_api_key
    assert service.list_visible(other) == ()
    assert service.list_visible(admin) == ()
    with pytest.raises(ConnectionAuthorizationError):
        service.chat(
            other, record.id, [{"role": "user", "content": "Hi"}], max_output_tokens=1
        )
    assert egress.calls == 0
    assert service.availability(owner) == ConnectionAvailability(
        ConnectionState.READY, record.id
    )
    assert service.availability(other).state is ConnectionState.UNAUTHORIZED


def test_instance_grants_and_individual_credentials(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, stranger = actors
    repository = MemoryConnections()
    egress = FakeEgress()
    service = _service(repository, policy, egress)
    record = replace(
        _record(None, frozenset({user.id})), identity_mode=IdentityMode.INDIVIDUAL
    )
    saved = service.save(admin, record, expected_version=None)
    assert service.availability(user).state is ConnectionState.DISABLED
    with pytest.raises(ConnectionAuthorizationError):
        service.set_credentials(
            stranger,
            saved.id,
            PlainCredentials(api_key=b"private"),
            expected_version=saved.version,
            credential_user_id=stranger.id,
        )
    saved = service.set_credentials(
        user,
        saved.id,
        PlainCredentials(api_key=b"private"),
        expected_version=saved.version,
        credential_user_id=user.id,
    )
    assert service.availability(user).state is ConnectionState.READY
    assert service.list_visible(user)[0].has_api_key
    assert service.availability(stranger).state is ConnectionState.UNAUTHORIZED
    assert service.discover_models(user, saved.id) == ("permitted",)
    assert service.chat(
        user, saved.id, [{"role": "user", "content": "Hi"}], max_output_tokens=1
    )
    assert egress.calls == 2


def test_revocation_fences_in_flight_result(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    egress = FakeEgress()
    service = _service(repository, policy, egress)
    record = service.save(
        admin,
        _record(None, frozenset({user.id})),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
    )

    def revoke() -> None:
        service.revoke_credentials(admin, record.id, expected_version=record.version)

    egress.before_return = revoke
    with pytest.raises(ConnectionConflictError, match="changed during model call"):
        service.chat(
            user, record.id, [{"role": "user", "content": "Hi"}], max_output_tokens=1
        )
    assert service.availability(user).state is ConnectionState.DISABLED
    assert not service.credential_presence(user, record.id).has_api_key


def test_model_cancellation_rejects_preflight_and_post_call_results(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    egress = FakeEgress()
    service = _service(repository, policy, egress)
    record = service.save(
        admin,
        _record(None, frozenset({user.id})),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
    )
    cancelled = Event()
    cancelled.set()
    with pytest.raises(EgressCancelledError):
        service.chat(
            user,
            record.id,
            [{"role": "user", "content": "Hi"}],
            max_output_tokens=1,
            cancel_event=cancelled,
        )
    with pytest.raises(EgressCancelledError):
        service.discover_models(user, record.id, cancel_event=cancelled)
    with pytest.raises(EgressCancelledError):
        service.test(user, record.id, cancel_event=cancelled)
    assert egress.calls == 0

    cancelled.clear()
    egress.before_return = cancelled.set
    with pytest.raises(EgressCancelledError):
        service.chat(
            user,
            record.id,
            [{"role": "user", "content": "Hi"}],
            max_output_tokens=1,
            cancel_event=cancelled,
        )
    assert not repository.get_outage(record.id, None)
    cancelled.clear()
    with pytest.raises(EgressCancelledError):
        service.discover_models(user, record.id, cancel_event=cancelled)
    assert not repository.get_outage(record.id, None)
    cancelled.clear()
    with pytest.raises(EgressCancelledError):
        service.test(user, record.id, cancel_event=cancelled)
    assert not repository.get_outage(record.id, None)


@pytest.mark.parametrize("occupied_slot", ["call", "dns"])
def test_local_capacity_keeps_connection_ready_without_provider_call(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
    occupied_slot: str,
) -> None:
    admin, user, _ = actors
    bounded = replace(policy, timeout_seconds=2, maximum_concurrent_calls=1)
    repository = MemoryConnections()
    resolver_calls = 0

    def resolve(_host: str, _port: int) -> tuple[str, ...]:
        nonlocal resolver_calls
        resolver_calls += 1
        return ("203.0.113.1",)

    egress = ConnectionEgress(bounded, resolve)
    service = _service(repository, bounded, egress)
    record = service.save(
        admin,
        _record(None, frozenset({user.id})),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
    )
    semaphore = egress._slots if occupied_slot == "call" else egress._resolver_slots
    assert semaphore.acquire(blocking=False)
    try:
        started_at = time.monotonic()
        with pytest.raises(EgressCapacityError, match="busy"):
            service.chat(
                user,
                record.id,
                [{"role": "user", "content": "Hi"}],
                max_output_tokens=1,
            )
        assert time.monotonic() - started_at < 0.5
    finally:
        semaphore.release()
    assert resolver_calls == 0
    assert not repository.get_outage(record.id, None)
    assert service.availability(user).state is ConnectionState.READY


def test_disabled_and_outage_states(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    service = _service(repository, policy, FakeEgress())
    assert service.availability(user).state is ConnectionState.UNCONFIGURED
    record = service.save(
        admin,
        _record(None, frozenset({user.id})),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
    )
    repository.outages.add((record.id, None))
    assert service.availability(user).state is ConnectionState.OUTAGE
    disabled = service.save(
        admin, replace(record, enabled=False), expected_version=record.version
    )
    assert disabled.generation > record.generation
    assert service.availability(user).state is ConnectionState.DISABLED


def test_provider_failure_sets_durable_outage_and_success_clears_it(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    egress = FakeEgress()
    service = _service(repository, policy, egress)
    record = service.save(
        admin,
        _record(None, frozenset({user.id})),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
    )

    def fail() -> None:
        raise EgressUnavailableError("Model provider is unavailable")

    egress.before_return = fail
    with pytest.raises(EgressUnavailableError):
        service.test(user, record.id)
    assert service.availability(user).state is ConnectionState.OUTAGE
    egress.before_return = None
    service.test(user, record.id)
    assert service.availability(user).state is ConnectionState.READY


def test_individual_outage_does_not_affect_another_identity(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, first, third = actors
    second = ConnectionActor(third.id, False, False)
    repository = MemoryConnections()
    egress = FakeEgress()
    service = _service(repository, policy, egress)
    record = service.save(
        admin,
        replace(
            _record(None, frozenset({first.id, second.id})),
            identity_mode=IdentityMode.INDIVIDUAL,
        ),
        expected_version=None,
    )
    record = service.set_credentials(
        first,
        record.id,
        PlainCredentials(api_key=b"private"),
        expected_version=record.version,
        credential_user_id=first.id,
    )
    record = service.set_credentials(
        second,
        record.id,
        PlainCredentials(api_key=b"private"),
        expected_version=record.version,
        credential_user_id=second.id,
    )
    repository.outages.add((record.id, first.id))
    assert service.availability(first).state is ConnectionState.OUTAGE
    assert service.availability(second).state is ConnectionState.READY


def test_manager_can_discover_catalog_before_permitting_models(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    service = _service(repository, policy, FakeEgress())
    record = service.save(
        admin,
        replace(
            _record(None, frozenset({user.id})),
            permitted_models=(),
            selected_model=None,
        ),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
    )
    assert service.discover_models(admin, record.id) == ("permitted", "other")
    assert service.discover_models(user, record.id) == ()


def test_personal_permission_revocation_fences_pending_result(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    _, owner, _ = actors
    repository = MemoryConnections()
    repository.personal_permissions.add(owner.id)
    egress = FakeEgress()
    service = _service(repository, policy, egress)
    record = service.save(
        owner,
        _record(owner.id, frozenset()),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
        credential_user_id=owner.id,
    )
    egress.before_return = lambda: repository.personal_permissions.remove(owner.id)
    with pytest.raises(ConnectionConflictError):
        service.chat(
            owner, record.id, [{"role": "user", "content": "Hi"}], max_output_tokens=1
        )
    assert service.availability(owner).state is ConnectionState.UNAUTHORIZED


def test_connection_revoke_disables_and_clears_grants(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    service = _service(repository, policy, FakeEgress())
    record = service.save(
        admin,
        _record(None, frozenset({user.id})),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
    )
    revoked = service.revoke(admin, record.id, expected_version=record.version)
    assert not revoked.enabled
    assert not revoked.allowed_user_ids
    assert revoked.generation > record.generation
    assert service.availability(user).state is ConnectionState.UNAUTHORIZED
    assert repository.get_credentials(record.id, None) is None


def test_revoke_destroys_every_individual_credential_before_reenable(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, first, third = actors
    second = ConnectionActor(third.id, False, False)
    repository = MemoryConnections()
    egress = FakeEgress()
    service = _service(repository, policy, egress)
    record = service.save(
        admin,
        replace(
            _record(None, frozenset({first.id, second.id})),
            identity_mode=IdentityMode.INDIVIDUAL,
        ),
        expected_version=None,
    )
    for user in (first, second):
        record = service.set_credentials(
            user,
            record.id,
            PlainCredentials(api_key=b"private"),
            expected_version=record.version,
            credential_user_id=user.id,
        )
    revoked = service.revoke(admin, record.id, expected_version=record.version)
    assert repository.get_credentials(record.id, first.id) is None
    assert repository.get_credentials(record.id, second.id) is None
    restored = service.save(
        admin,
        replace(
            revoked, enabled=True, allowed_user_ids=frozenset({first.id, second.id})
        ),
        expected_version=revoked.version,
    )
    assert restored.generation > revoked.generation
    assert service.availability(first).state is ConnectionState.DISABLED
    with pytest.raises(ConnectionConfigurationError, match="not configured"):
        service.chat(
            first,
            record.id,
            [{"role": "user", "content": "Hi"}],
            max_output_tokens=1,
        )
    assert egress.calls == 0


def test_api_key_header_injection_is_rejected_before_storage(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    service = _service(repository, policy, FakeEgress())
    record = _record(None, frozenset({user.id}))
    with pytest.raises(ConnectionConfigurationError, match="API credential is invalid"):
        service.save(
            admin,
            record,
            expected_version=None,
            credentials=PlainCredentials(api_key=b"private\r\nInjected: yes"),
        )
    assert repository.records == {}


def test_partial_rotation_preserves_unsubmitted_sealed_fields(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    service = _service(repository, policy, FakeEgress())
    record = service.save(
        admin,
        _record(None, frozenset({user.id})),
        expected_version=None,
        credentials=PlainCredentials(
            api_key=b"private",
            client_certificate=b"old-cert",
            client_private_key=b"old-key",
            ca_bundle=b"old-ca",
        ),
    )
    original = repository.get_credentials(record.id, None)
    assert original is not None
    updated = service.set_credentials(
        admin,
        record.id,
        PlainCredentials(api_key=b"rotated"),
        expected_version=record.version,
    )
    current = repository.get_credentials(record.id, None)
    assert current is not None
    assert current.api_key != original.api_key
    assert current.client_certificate == original.client_certificate
    assert current.client_private_key == original.client_private_key
    assert current.ca_bundle == original.ca_bundle
    updated = service.set_credentials(
        admin,
        updated.id,
        PlainCredentials(ca_bundle=b"new-ca"),
        expected_version=updated.version,
    )
    merged = repository.get_credentials(updated.id, None)
    assert merged is not None
    assert merged.api_key == current.api_key
    assert merged.ca_bundle != current.ca_bundle
    assert service.credential_presence(user, updated.id).has_client_certificate
    with pytest.raises(ConnectionConfigurationError, match="No credential"):
        service.set_credentials(
            admin, updated.id, PlainCredentials(), expected_version=updated.version
        )


def test_stale_version_and_identity_mode_change_are_rejected(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    service = _service(repository, policy, FakeEgress())
    record = service.save(
        admin, _record(None, frozenset({user.id})), expected_version=None
    )
    with pytest.raises(ConnectionConflictError):
        service.save(admin, record, expected_version=None)
    with pytest.raises(ConnectionConflictError):
        service.save(admin, record, expected_version=record.version - 1)
    with pytest.raises(ConnectionConflictError):
        service.save(
            admin,
            replace(record, identity_mode=IdentityMode.INDIVIDUAL),
            expected_version=record.version,
        )


def test_manager_and_missing_credential_guards(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    service = _service(repository, policy, FakeEgress())
    with pytest.raises(ConnectionAuthorizationError):
        service.save(user, _record(None, frozenset({user.id})), expected_version=None)
    record = service.save(
        admin, _record(None, frozenset({user.id})), expected_version=None
    )
    with pytest.raises(ConnectionConfigurationError, match="not configured"):
        service.test(user, record.id)
    with pytest.raises(ConnectionNotFoundError):
        service.test(user, uuid4())
    with pytest.raises(ConnectionAuthorizationError):
        service.credential_presence(actors[2], record.id)
    with pytest.raises(ConnectionAuthorizationError):
        service.set_credentials(
            user,
            record.id,
            PlainCredentials(api_key=b"private"),
            expected_version=record.version,
        )


def test_model_and_credential_configuration_validation(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    service = _service(repository, policy, FakeEgress())
    record = _record(None, frozenset({user.id}))
    with pytest.raises(ConnectionConfigurationError, match="Permitted model list"):
        service.save(
            admin,
            replace(
                record, permitted_models=("model with space",), selected_model=None
            ),
            expected_version=None,
        )
    with pytest.raises(EgressPolicyError):
        service.save(
            admin,
            replace(record, endpoint="https://other.example/v1"),
            expected_version=None,
        )
    with pytest.raises(ConnectionConfigurationError, match="required"):
        service.save(
            admin,
            record,
            expected_version=None,
            credentials=PlainCredentials(ca_bundle=b"CA"),
        )
    with pytest.raises(ConnectionConfigurationError, match="exceeds"):
        service.save(
            admin,
            record,
            expected_version=None,
            credentials=PlainCredentials(api_key=b"x" * 4097),
        )


@pytest.mark.parametrize(
    "change",
    [
        {"name": ""},
        {"version": -1},
        {"permitted_models": ("permitted", "permitted")},
        {"permitted_models": ("other",)},
        {"owner_id": uuid4()},
    ],
)
def test_invalid_connection_metadata_is_rejected(change: dict[str, object]) -> None:
    with pytest.raises(ConnectionConfigurationError):
        replace(_record(None, frozenset()), **change)


@pytest.mark.parametrize(
    "change",
    [
        {"owner_id": None},
        {"identity_mode": IdentityMode.SHARED},
        {"allowed_user_ids": frozenset({uuid4()})},
    ],
)
def test_personal_metadata_rejects_missing_owner_shared_identity_and_grants(
    change: dict[str, object],
) -> None:
    with pytest.raises(ConnectionConfigurationError, match="Personal connection"):
        replace(_record(uuid4(), frozenset()), **change)


def test_create_version_and_credential_mutations_fail_closed(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    service = _service(repository, policy, FakeEgress())
    candidate = _record(None, frozenset({user.id}))
    with pytest.raises(ConnectionConflictError):
        service.save(admin, candidate, expected_version=0)
    assert repository.records == {}

    shared = service.save(admin, candidate, expected_version=None)
    with pytest.raises(ConnectionConfigurationError, match="API key or client"):
        service.set_credentials(
            admin,
            shared.id,
            PlainCredentials(ca_bundle=b"CA only"),
            expected_version=shared.version,
        )
    assert repository.get_credentials(shared.id, None) is None
    with pytest.raises(ConnectionAuthorizationError):
        service.revoke(user, shared.id, expected_version=shared.version)
    assert repository.records[shared.id] == shared

    individual = service.save(
        admin,
        replace(
            _record(None, frozenset({user.id})), identity_mode=IdentityMode.INDIVIDUAL
        ),
        expected_version=None,
    )
    individual = service.set_credentials(
        user,
        individual.id,
        PlainCredentials(api_key=b"private"),
        expected_version=individual.version,
        credential_user_id=user.id,
    )
    cleared = service.revoke_credentials(
        user,
        individual.id,
        expected_version=individual.version,
        credential_user_id=user.id,
    )
    assert cleared.generation > individual.generation
    assert repository.get_credentials(individual.id, user.id) == EncryptedCredentials()


def test_model_calls_reject_unselected_unpermitted_disabled_and_revoked_personal(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    egress = FakeEgress()
    service = _service(repository, policy, egress)
    unselected = service.save(
        admin,
        replace(_record(None, frozenset({user.id})), selected_model=None),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
    )
    with pytest.raises(ConnectionConfigurationError, match="permitted model"):
        service.chat(
            user,
            unselected.id,
            [{"role": "user", "content": "Hi"}],
            max_output_tokens=1,
        )
    with pytest.raises(ConnectionConfigurationError, match="permitted model"):
        service.test(user, unselected.id, model="unapproved")
    disabled = service.save(
        admin,
        replace(unselected, enabled=False),
        expected_version=unselected.version,
    )
    with pytest.raises(ConnectionConfigurationError, match="disabled"):
        service.chat(
            user,
            disabled.id,
            [{"role": "user", "content": "Hi"}],
            max_output_tokens=1,
        )

    repository.personal_permissions.add(user.id)
    personal = service.save(
        user,
        _record(user.id, frozenset()),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
        credential_user_id=user.id,
    )
    repository.personal_permissions.remove(user.id)
    with pytest.raises(ConnectionAuthorizationError):
        service.chat(
            user,
            personal.id,
            [{"role": "user", "content": "Hi"}],
            max_output_tokens=1,
        )
    with pytest.raises(ConnectionAuthorizationError):
        service.set_credentials(
            user,
            personal.id,
            PlainCredentials(api_key=b"rotated"),
            expected_version=personal.version,
            credential_user_id=user.id,
        )
    assert egress.calls == 0


def test_egress_rejects_invalid_chat_without_network(
    policy: ConnectionPolicy, mocker: MockerFixture
) -> None:
    egress = ConnectionEgress(policy)
    request = mocker.patch.object(egress, "_request")
    credentials = PlainCredentials(api_key=b"private")
    for model, messages, tokens in (
        ("bad model", [{"role": "user", "content": "Hi"}], 1),
        ("permitted", [], 1),
        ("permitted", [{"role": "tool", "content": "Hi"}], 1),
        ("permitted", [{"role": "user", "content": "Hi"}], 0),
    ):
        with pytest.raises(EgressPolicyError):
            egress.chat(
                "https://models.example/v1",
                credentials,
                model=model,
                messages=messages,
                max_output_tokens=tokens,
            )
    request.assert_not_called()


def test_egress_rejects_invalid_model_responses(
    policy: ConnectionPolicy, mocker: MockerFixture
) -> None:
    egress = ConnectionEgress(policy)
    response = mocker.patch.object(egress, "_request")
    for payload in ({}, {"data": "wrong"}, {"data": [{"id": "bad id"}]}):
        response.return_value = payload
        with pytest.raises(EgressResponseError):
            egress.discover_models(
                "https://models.example/v1", PlainCredentials(api_key=b"private")
            )
    response.return_value = {"choices": []}
    with pytest.raises(EgressResponseError):
        egress.chat(
            "https://models.example/v1",
            PlainCredentials(api_key=b"private"),
            model="permitted",
            messages=[{"role": "user", "content": "Hi"}],
            max_output_tokens=1,
        )


@pytest.mark.parametrize(
    "url",
    [
        "http://models.example/v1",
        "https://models.example.evil/v1",
        "https://models.example@evil.test/v1",
        "https://models.example/v1?next=evil",
        "https://models.example/v1/../private",
        "https://models.example:0/v1",
        "https://models.example:/v1",
    ],
)
def test_endpoint_policy_rejects_unapproved_urls(
    url: str, policy: ConnectionPolicy
) -> None:
    with pytest.raises(EgressPolicyError):
        validate_endpoint(url, policy)


def test_dns_rechecks_every_request_and_rejects_mixed_answers(
    policy: ConnectionPolicy,
) -> None:
    answers = iter([("203.0.113.1",), ("203.0.113.1", "127.0.0.1")])
    egress = ConnectionEgress(policy, lambda _host, _port: next(answers))
    endpoint = validate_endpoint("https://models.example/v1", policy)
    assert str(egress._resolve(endpoint)) == "203.0.113.1"
    with pytest.raises(EgressPolicyError, match="address is not allowed"):
        egress._resolve(endpoint)


def test_stalled_dns_is_timed_out_without_unbounded_threads(
    policy: ConnectionPolicy,
) -> None:
    released = Event()
    started = Event()
    calls = 0

    def resolve(_host: str, _port: int) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        started.set()
        released.wait(timeout=2)
        return ("203.0.113.1",)

    bounded = replace(policy, timeout_seconds=0.05, maximum_concurrent_calls=1)
    egress = ConnectionEgress(bounded, resolve)
    endpoint = validate_endpoint("https://models.example/v1", bounded)
    try:
        with pytest.raises(EgressUnavailableError, match=r"resolved|timed out"):
            egress._resolve(endpoint)
        assert started.is_set()
        with pytest.raises(EgressCapacityError, match="resolution is busy"):
            egress._resolve(endpoint)
        assert calls == 1
    finally:
        released.set()


def test_secret_binding_tamper_and_private_key_file(tmp_path) -> None:
    cipher = SecretCipher(b"x" * 32)
    assert "private" not in repr(PlainCredentials(api_key=b"private"))
    first, second = uuid4(), uuid4()
    ciphertext = cipher.seal(first, "api_key", b"private")
    assert cipher.open(first, "api_key", ciphertext) == b"private"
    with pytest.raises(SecretError):
        cipher.open(second, "api_key", ciphertext)
    with pytest.raises(SecretError):
        cipher.open(first, "api_key", ciphertext[:-1] + bytes([ciphertext[-1] ^ 1]))
    key_path = tmp_path / "key"
    key_path.write_text((b"x" * 32).hex() + "\n", encoding="ascii")
    key_path.chmod(0o600)
    assert (
        SecretCipher.from_key_file(key_path).open(first, "api_key", ciphertext)
        == b"private"
    )
    key_path.chmod(0o644)
    with pytest.raises(SecretError, match="not private"):
        SecretCipher.from_key_file(key_path)
    for mode in (0o660, 0o750):
        key_path.chmod(mode)
        with pytest.raises(SecretError, match="not private"):
            SecretCipher.from_key_file(key_path)
    key_path.chmod(0o640)
    assert (
        SecretCipher.from_key_file(key_path).open(first, "api_key", ciphertext)
        == b"private"
    )


def test_secret_bundle_and_invalid_key_sources(tmp_path) -> None:
    connection_id = uuid4()
    cipher = SecretCipher(b"x" * 32)
    credentials = PlainCredentials(
        api_key=b"private",
        client_certificate=b"certificate",
        client_private_key=b"private-key",
        ca_bundle=b"ca",
    )
    encrypted = cipher.seal_credentials(connection_id, credentials)
    assert "private" not in repr(encrypted)
    assert cipher.open_credentials(connection_id, encrypted) == credentials
    with pytest.raises(SecretError):
        cipher.open(connection_id, "api_key", b"invalid")
    with pytest.raises(SecretError):
        cipher.seal(connection_id, "invalid_field", b"value")
    with pytest.raises(SecretError):
        cipher.seal(connection_id, "api_key", b"")
    with pytest.raises(ValueError):
        SecretCipher(b"short")
    with pytest.raises(ValueError):
        PlainCredentials(client_certificate=b"cert")
    with pytest.raises(ValueError):
        PlainCredentials(api_key=b"")
    with pytest.raises(SecretError):
        cipher.open_credentials(
            connection_id,
            EncryptedCredentials(client_certificate=encrypted.client_certificate),
        )

    key_path = tmp_path / "key"
    with pytest.raises(SecretError, match="unavailable"):
        SecretCipher.from_key_file(key_path)
    key_path.write_text("invalid", encoding="ascii")
    key_path.chmod(0o600)
    with pytest.raises(SecretError, match="invalid"):
        SecretCipher.from_key_file(key_path)
    key_path.write_text("g" * 64, encoding="ascii")
    with pytest.raises(SecretError, match="invalid"):
        SecretCipher.from_key_file(key_path)
    alias = tmp_path / "link"
    alias.symlink_to(key_path)
    with pytest.raises(SecretError, match="unavailable"):
        SecretCipher.from_key_file(alias)


def test_wrong_operator_key_produces_safe_connection_error(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    admin, user, _ = actors
    repository = MemoryConnections()
    service = _service(repository, policy, FakeEgress())
    record = service.save(
        admin,
        _record(None, frozenset({user.id})),
        expected_version=None,
        credentials=PlainCredentials(api_key=b"private"),
    )
    wrong_key_service = ConnectionService(
        repository, SecretCipher(b"z" * 32), FakeEgress(), policy
    )
    with pytest.raises(ConnectionConfigurationError) as error:
        wrong_key_service.test(user, record.id)
    assert "private" not in str(error.value)
    assert "unavailable" in str(error.value)


def test_authorized_pagination_and_exact_lookup_cross_database_pages(
    policy: ConnectionPolicy,
    actors: tuple[ConnectionActor, ConnectionActor, ConnectionActor],
) -> None:
    _, user, other = actors
    repository = MemoryConnections()
    service = _service(repository, policy, FakeEgress())
    authorized_ids: list[UUID] = []
    for index in range(130):
        allowed = frozenset({user.id}) if index % 2 == 0 else frozenset({other.id})
        record = replace(
            _record(None, allowed),
            id=UUID(int=index + 1),
            name=f"Connection {index + 1}",
            selected_model=None,
        )
        repository.records[record.id] = record
        if user.id in allowed:
            authorized_ids.append(record.id)
    last_id = authorized_ids[-1]
    repository.records[last_id] = replace(
        repository.records[last_id], selected_model="permitted"
    )
    repository.credentials[(last_id, None)] = SecretCipher(b"s" * 32).seal_credentials(
        last_id, PlainCredentials(api_key=b"private")
    )

    page = service.list_visible(user, limit=20, offset=40)
    assert tuple(record.id for record in page) == tuple(authorized_ids[40:60])
    assert len(page) == 20
    assert all(not record.allowed_user_ids for record in page)
    assert service.get_visible(user, last_id).id == last_id
    with pytest.raises(ConnectionNotFoundError):
        service.get_visible(user, UUID(int=128))
    assert service.availability(user) == ConnectionAvailability(
        ConnectionState.READY, last_id
    )
    with pytest.raises(ValueError, match="pagination"):
        service.list_visible(user, limit=101)
    with pytest.raises(ValueError, match="pagination"):
        service.list_visible(user, offset=-1)
