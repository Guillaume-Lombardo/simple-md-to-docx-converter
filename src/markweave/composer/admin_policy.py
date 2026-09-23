"""Administrator approval inside an immutable deployment Composer ceiling."""

from __future__ import annotations

import ipaddress
import queue
import socket
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from ipaddress import IPv4Network, IPv6Network
from urllib.parse import urlsplit
from uuid import UUID

from markweave.composer.connections import ConnectionConfigurationError
from markweave.composer.egress import (
    AllowedDestination,
    ConnectionPolicy,
    EgressPolicyError,
    validate_endpoint,
)
from markweave.persistence.composer.admin_policy import (
    SqlComposerAdminPolicyRepository,
    StoredAdminPolicy,
)

_MAX_POLICY_ENTRIES = 100
_MAX_RESOLVED_ADDRESSES = 16
_RESOLVE_TIMEOUT_SECONDS = 5.0
_RESOLVE_SLOTS = threading.BoundedSemaphore(4)
_Network = IPv4Network | IPv6Network
Resolver = Callable[[str, int], Sequence[str]]


@dataclass(frozen=True, slots=True)
class AdminPolicy:
    mode: str
    enabled: bool
    destinations: tuple[str, ...]
    networks: tuple[str, ...]
    version: int


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    policy: ConnectionPolicy
    enabled: bool
    version: int


class ComposerAdminPolicy:
    """Read current SQL approval on every model step and policy mutation."""

    def __init__(
        self,
        repository: SqlComposerAdminPolicyRepository,
        ceiling: ConnectionPolicy,
        *,
        delegated: bool,
        resolver: Resolver | None = None,
    ) -> None:
        self._repository = repository
        self._ceiling = ceiling
        self._delegated = delegated
        self._resolver = resolver or _resolve_host

    def read(self) -> AdminPolicy:
        stored = self._repository.get()
        if stored is None:
            return AdminPolicy(
                mode="delegated" if self._delegated else "operator",
                enabled=not self._delegated,
                destinations=(
                    () if self._delegated else _destination_strings(self._ceiling)
                ),
                networks=(
                    ()
                    if self._delegated
                    else tuple(
                        str(network) for network in self._ceiling.allowed_networks
                    )
                ),
                version=0,
            )
        if not self._delegated:
            try:
                self._validate_lists(stored.destinations, stored.networks)
            except EgressPolicyError:
                # A changed deployment ceiling revokes the old approval. Show
                # its current bounds for an explicit administrator re-enable.
                return AdminPolicy(
                    mode="operator",
                    enabled=False,
                    destinations=_destination_strings(self._ceiling),
                    networks=tuple(
                        str(network) for network in self._ceiling.allowed_networks
                    ),
                    version=stored.version,
                )
        return AdminPolicy(
            mode="delegated" if self._delegated else "operator",
            enabled=stored.enabled,
            destinations=stored.destinations,
            networks=stored.networks,
            version=stored.version,
        )

    def snapshot(self) -> PolicySnapshot:
        current = self.read()
        destinations, networks = self._validate_lists(
            current.destinations, current.networks
        )
        return PolicySnapshot(
            replace(
                self._ceiling,
                destinations=frozenset(destinations),
                allowed_networks=networks,
            ),
            current.enabled,
            current.version,
        )

    def require_enabled(self) -> ConnectionPolicy:
        snapshot = self.snapshot()
        if not snapshot.enabled:
            raise ConnectionConfigurationError("Composer model access is disabled")
        return snapshot.policy

    def write(
        self,
        *,
        enabled: bool,
        destinations: tuple[str, ...],
        networks: tuple[str, ...],
        expected_version: int,
        actor_id: UUID,
    ) -> AdminPolicy:
        parsed_destinations, parsed_networks = self._validate_lists(
            destinations, networks
        )
        if enabled and (not parsed_destinations or not parsed_networks):
            raise EgressPolicyError("Approve a destination and its addresses first")
        saved = self._repository.put(
            enabled=enabled,
            destinations=tuple(sorted(destinations)),
            networks=tuple(sorted(networks)),
            expected_version=expected_version,
            actor_id=actor_id,
            default_enabled=not self._delegated,
        )
        return self._project(saved)

    def resolve(self, endpoint: str) -> tuple[str, tuple[str, ...]]:
        """Preview DNS addresses without opening a provider connection."""

        try:
            parsed = urlsplit(endpoint)
            if parsed.hostname is None:
                raise ValueError
            port = 443 if parsed.port is None else parsed.port
            destination = AllowedDestination(parsed.hostname, port)
            validate_endpoint(
                endpoint,
                replace(self._ceiling, destinations=frozenset({destination})),
            )
        except ValueError:
            raise EgressPolicyError("Connection endpoint is invalid") from None
        if not _RESOLVE_SLOTS.acquire(blocking=False):
            raise ConnectionConfigurationError("Destination resolution is busy")
        result: queue.Queue[Sequence[str] | Exception] = queue.Queue(maxsize=1)

        def work() -> None:
            try:
                result.put(self._resolver(destination.host, destination.port))
            except Exception as error:
                result.put(error)
            finally:
                _RESOLVE_SLOTS.release()

        try:
            threading.Thread(target=work, daemon=True).start()
        except RuntimeError:
            _RESOLVE_SLOTS.release()
            raise ConnectionConfigurationError(
                "Destination resolution failed"
            ) from None
        try:
            addresses = result.get(timeout=_RESOLVE_TIMEOUT_SECONDS)
        except queue.Empty:
            raise ConnectionConfigurationError(
                "Destination resolution timed out"
            ) from None
        if isinstance(addresses, Exception):
            raise ConnectionConfigurationError("Destination could not be resolved")
        try:
            unique = sorted(
                {ipaddress.ip_address(value) for value in addresses},
                key=lambda address: (address.version, int(address)),
            )
            if not unique or len(unique) > _MAX_RESOLVED_ADDRESSES:
                raise ValueError
            return (
                f"{destination.host}:{destination.port}",
                tuple(f"{address}/{address.max_prefixlen}" for address in unique),
            )
        except ValueError:
            raise ConnectionConfigurationError(
                "Destination DNS answer is invalid"
            ) from None

    def _validate_lists(
        self, destinations: tuple[str, ...], networks: tuple[str, ...]
    ) -> tuple[tuple[AllowedDestination, ...], tuple[_Network, ...]]:
        if (
            len(destinations) > _MAX_POLICY_ENTRIES
            or len(networks) > _MAX_POLICY_ENTRIES
            or len(set(destinations)) != len(destinations)
            or len(set(networks)) != len(networks)
        ):
            raise EgressPolicyError("Composer approval list is invalid")
        try:
            parsed_destinations = tuple(
                _parse_destination(item) for item in destinations
            )
            parsed_networks = tuple(
                ipaddress.ip_network(item, strict=True) for item in networks
            )
        except ValueError:
            raise EgressPolicyError("Composer approval list is invalid") from None
        if self._delegated:
            if any(
                network.prefixlen != network.max_prefixlen
                or network.network_address.is_loopback
                or network.network_address.is_link_local
                or network.network_address.is_multicast
                or network.network_address.is_unspecified
                for network in parsed_networks
            ):
                raise EgressPolicyError("Approve exact safe destination addresses")
        elif any(
            item not in self._ceiling.destinations for item in parsed_destinations
        ) or any(
            not any(
                _within(network, ceiling) for ceiling in self._ceiling.allowed_networks
            )
            for network in parsed_networks
        ):
            raise EgressPolicyError("Composer approval exceeds operator policy")
        return parsed_destinations, parsed_networks

    def _project(self, stored: StoredAdminPolicy) -> AdminPolicy:
        return AdminPolicy(
            "delegated" if self._delegated else "operator",
            stored.enabled,
            stored.destinations,
            stored.networks,
            stored.version,
        )


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


def _destination_strings(policy: ConnectionPolicy) -> tuple[str, ...]:
    return tuple(sorted(f"{item.host}:{item.port}" for item in policy.destinations))


def _within(network: _Network, ceiling: _Network) -> bool:
    if isinstance(network, IPv4Network) and isinstance(ceiling, IPv4Network):
        return network.subnet_of(ceiling)
    if isinstance(network, IPv6Network) and isinstance(ceiling, IPv6Network):
        return network.subnet_of(ceiling)
    return False


def _resolve_host(host: str, port: int) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                str(address[4][0])
                for address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
                if address[0] in (socket.AF_INET, socket.AF_INET6)
            }
        )
    )
