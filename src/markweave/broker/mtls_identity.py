"""TLS identities, certificate pinning and authenticated broker peer contexts."""

from __future__ import annotations

import ipaddress
import math
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import markweave.broker.mtls_control as _mtls_control
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import AuthenticatedPrincipal

_DIGEST_PREFIX = _mtls_control._DIGEST_PREFIX

_PIN_LENGTH = _mtls_control._PIN_LENGTH

_sha256 = _mtls_control._sha256

_valid_digest = _mtls_control._valid_digest

MTLS_ALPN: Final = "markweave-reverse-broker-mtls/1"

_IPV4_VERSION: Final = 4

_MAX_PORT: Final = 65535

_MAX_PINS: Final = 2

_MAX_URI_LENGTH: Final = 255

_SERVER_CONTEXT_TOKEN = object()


@dataclass(frozen=True, slots=True)
class MtlsEndpoint:
    """Explicit canonical IPv4 endpoint for one inert mTLS boundary."""

    host: str
    port: int

    def __post_init__(self) -> None:
        if type(self.host) is not str:
            raise ValueError("Broker mTLS endpoint is invalid")
        try:
            address = ipaddress.ip_address(self.host)
        except (TypeError, ValueError) as error:
            raise ValueError("Broker mTLS endpoint is invalid") from error
        if (
            type(self.host) is not str
            or address.version != _IPV4_VERSION
            or str(address) != self.host
            or type(self.port) is not int
            or not 0 <= self.port <= _MAX_PORT
        ):
            raise ValueError("Broker mTLS endpoint is invalid")


@dataclass(frozen=True, slots=True)
class MtlsTransportLimits:
    """Deployment-supplied mTLS bounds, with no production defaults."""

    operation_timeout_seconds: float
    shutdown_timeout_seconds: float
    max_handshakes: int
    max_pending_exchanges: int
    max_handlers: int
    listen_backlog: int

    def __post_init__(self) -> None:
        numbers = (self.operation_timeout_seconds, self.shutdown_timeout_seconds)
        counts = (
            self.max_handshakes,
            self.max_pending_exchanges,
            self.max_handlers,
            self.listen_backlog,
        )
        if any(
            type(value) not in {int, float} or value <= 0 or not math.isfinite(value)
            for value in numbers
        ) or any(type(value) is not int or value <= 0 for value in counts):
            raise ValueError("Broker mTLS transport limits are invalid")


@dataclass(frozen=True, slots=True)
class MtlsLocalIdentity:
    """Required local certificate material and its stable protocol principal."""

    ca_certificate: Path
    certificate_chain: Path
    private_key: Path
    uri_san: str
    principal: AuthenticatedPrincipal

    def __post_init__(self) -> None:
        if (
            not all(
                isinstance(path, Path) and path.is_absolute()
                for path in (
                    self.ca_certificate,
                    self.certificate_chain,
                    self.private_key,
                )
            )
            or not _valid_uri_san(self.uri_san)
            or type(self.principal) is not AuthenticatedPrincipal
        ):
            raise ValueError("Broker mTLS local identity is invalid")


@dataclass(frozen=True, slots=True)
class MtlsPeerIdentity:
    """Exact peer role and current/next leaf-certificate pins."""

    uri_san: str
    leaf_certificate_sha256: tuple[str, ...]
    principal: AuthenticatedPrincipal

    def __post_init__(self) -> None:
        pins = self.leaf_certificate_sha256
        if (
            not _valid_uri_san(self.uri_san)
            or type(pins) is not tuple
            or not 1 <= len(pins) <= _MAX_PINS
            or len(set(pins)) != len(pins)
            or any(not _valid_digest(pin) for pin in pins)
            or type(self.principal) is not AuthenticatedPrincipal
        ):
            raise ValueError("Broker mTLS peer identity is invalid")


class MtlsServerContext:
    """Opaque, fully loaded server TLS context safe to retain after FD closure."""

    __slots__ = ("_context", "_local_identity")

    def __init__(
        self, context: ssl.SSLContext, local_identity: MtlsLocalIdentity, token: object
    ) -> None:
        if token is not _SERVER_CONTEXT_TOKEN:
            raise ValueError("Broker mTLS server context is invalid")
        self._context = context
        self._local_identity = local_identity


def _valid_uri_san(value: object) -> bool:
    if type(value) is not str or not value.isascii() or len(value) > _MAX_URI_LENGTH:
        return False
    try:
        parsed = urlsplit(value)
        return (
            parsed.scheme == "spiffe"
            and bool(parsed.hostname)
            and parsed.netloc == parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.port is None
            and parsed.path.startswith("/")
            and parsed.path != "/"
            and "//" not in parsed.path
            and all(
                character.isalnum() or character in "/._-" for character in parsed.path
            )
            and not parsed.query
            and not parsed.fragment
            and urlunsplit(parsed) == value
        )
    except ValueError:
        return False


def leaf_certificate_sha256(certificate_der: bytes) -> str:
    """Return the canonical exact-leaf certificate pin."""

    if type(certificate_der) is not bytes or not certificate_der:
        raise ValueError("Broker mTLS leaf certificate is invalid")
    return _sha256(certificate_der)


def _tls_context(local: MtlsLocalIdentity, *, server: bool) -> ssl.SSLContext:
    protocol = ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT
    context = ssl.SSLContext(protocol)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    if not server:
        context.check_hostname = False
    context.verify_flags |= ssl.VERIFY_X509_STRICT
    context.options |= ssl.OP_NO_COMPRESSION | ssl.OP_NO_TICKET
    if server:
        context.num_tickets = 0
    try:
        context.load_verify_locations(cafile=str(local.ca_certificate))
        context.load_cert_chain(
            certfile=str(local.certificate_chain), keyfile=str(local.private_key)
        )
        context.set_alpn_protocols([MTLS_ALPN])
    except (OSError, ssl.SSLError, ValueError) as error:
        raise ValueError("Broker mTLS certificate material is invalid") from error
    return context


def _authenticate_peer(
    connection: ssl.SSLSocket, expected: MtlsPeerIdentity
) -> tuple[AuthenticatedPrincipal, str]:
    if (
        connection.version() != "TLSv1.3"
        or connection.selected_alpn_protocol() != MTLS_ALPN
    ):
        raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)
    certificate = connection.getpeercert()
    certificate_der = connection.getpeercert(binary_form=True)
    if (
        type(certificate) is not dict
        or certificate.get("subjectAltName") != (("URI", expected.uri_san),)
        or type(certificate_der) is not bytes
    ):
        raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)
    digest = leaf_certificate_sha256(certificate_der)
    if digest not in expected.leaf_certificate_sha256:
        raise BrokerError(BrokerErrorCategory.AUTHENTICATION_FAILED)
    return expected.principal, digest
