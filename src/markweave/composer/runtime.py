"""Build the optional Composer model boundary from explicit operator policy."""

from __future__ import annotations

from ipaddress import ip_network
from urllib.parse import urlsplit

from markweave.config import ConfigurationError, Settings

from .admin_policy import ComposerAdminPolicy
from .connections import (
    AllowedDestination,
    ConnectionPolicy,
    ConnectionRepository,
    ConnectionService,
)
from .egress import ConnectionEgress
from .secrets import SecretCipher, SecretError


def build_connection_service(
    settings: Settings,
    repository: ConnectionRepository,
    admin_policy: ComposerAdminPolicy | None = None,
) -> ConnectionService | None:
    """Return no model client when Composer egress is disabled."""
    if not settings.composer_enabled:
        return None
    try:
        policy = build_connection_policy(settings)
        key_path = settings.composer_secret_key_path
        if key_path is None:
            raise ValueError("missing key path")
        cipher = SecretCipher.from_key_file(key_path)
        bind_key = getattr(repository, "bind_key_identity", None)
        if callable(bind_key):
            bind_key(cipher)
    except OSError, SecretError, TypeError, ValueError:
        raise ConfigurationError("Invalid Composer connection configuration") from None
    if admin_policy is None:
        return ConnectionService(repository, cipher, ConnectionEgress(policy), policy)

    def state() -> tuple[ConnectionPolicy, bool, int]:
        snapshot = admin_policy.snapshot()
        return snapshot.policy, snapshot.enabled, snapshot.version

    return ConnectionService(
        repository,
        cipher,
        ConnectionEgress(policy, policy_provider=admin_policy.require_enabled),
        policy,
        policy_state=state,
    )


def build_connection_policy(settings: Settings) -> ConnectionPolicy:
    """Validate the immutable deployment ceiling and bounded call limits."""

    try:
        destinations = frozenset(
            _parse_destination(value)
            for value in settings.composer_allowed_destinations or ()
        )
        networks = tuple(
            ip_network(value, strict=True)
            for value in settings.composer_allowed_networks or ()
        )
        return ConnectionPolicy(
            destinations=destinations,
            allowed_networks=networks,
            maximum_request_bytes=_required(settings.composer_maximum_request_bytes),
            maximum_response_bytes=_required(settings.composer_maximum_response_bytes),
            maximum_models=_required(settings.composer_maximum_models),
            maximum_allowed_users=_required(settings.composer_maximum_allowed_users),
            maximum_model_name_length=_required(
                settings.composer_maximum_model_name_length
            ),
            maximum_credential_bytes=_required(
                settings.composer_maximum_credential_bytes
            ),
            maximum_output_tokens=_required(settings.composer_maximum_output_tokens),
            maximum_concurrent_calls=_required(
                settings.composer_maximum_concurrent_calls
            ),
            timeout_seconds=_required(settings.composer_timeout_seconds),
        )
    except TypeError, ValueError:
        raise ConfigurationError("Invalid Composer connection configuration") from None


def _parse_destination(value: str) -> AllowedDestination:
    parsed = urlsplit(f"https://{value}")
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid destination")
    return AllowedDestination(parsed.hostname, parsed.port)


def _required[Number: (int, float)](value: Number | None) -> Number:
    if value is None:
        raise ValueError("Incomplete Composer policy")
    return value
