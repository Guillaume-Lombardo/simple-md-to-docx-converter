"""Destination-pinned, bounded OpenAI-compatible HTTPS egress."""

from __future__ import annotations

import http.client
import ipaddress
import json
import math
import os
import queue
import re
import socket
import ssl
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Any
from urllib.parse import SplitResult, urlsplit

from markweave.composer.secrets import PlainCredentials

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network
Resolver = Callable[[str, int], Sequence[str]]
_HOST_RE = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
_PATH_RE = re.compile(r"/[A-Za-z0-9._~/-]*\Z")
_MAX_PORT = 65535
_MIN_VISIBLE_ASCII = 33
_MAX_VISIBLE_ASCII = 126
_SUCCESS_STATUS = 200
_REDIRECT_MIN = 300
_REDIRECT_MAX = 400
_CANCEL_POLL_SECONDS = 0.05


class EgressPolicyError(ValueError):
    """A connection endpoint is outside the operator policy."""


class EgressUnavailableError(Exception):
    """The configured provider could not complete a bounded call."""


class EgressCapacityError(Exception):
    """Local Composer call or resolver capacity is exhausted."""


class EgressResponseError(Exception):
    """The provider returned an invalid or disallowed response."""


class EgressCancelledError(Exception):
    """A trusted caller cancelled the model operation before publication."""


@dataclass(frozen=True, slots=True)
class AllowedDestination:
    host: str
    port: int

    def __post_init__(self) -> None:
        if self.host != self.host.lower() or not _HOST_RE.fullmatch(self.host):
            raise ValueError("Allowed destination host is invalid")
        if type(self.port) is not int or not 1 <= self.port <= _MAX_PORT:
            raise ValueError("Allowed destination port is invalid")


@dataclass(frozen=True, slots=True)
class ConnectionPolicy:
    """Explicit operator limits; an empty allowlist is closed."""

    destinations: frozenset[AllowedDestination]
    allowed_networks: tuple[IPNetwork, ...]
    maximum_request_bytes: int
    maximum_response_bytes: int
    maximum_models: int
    maximum_model_name_length: int
    maximum_credential_bytes: int
    maximum_output_tokens: int
    maximum_concurrent_calls: int
    maximum_allowed_users: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, AllowedDestination) for value in self.destinations
        ):
            raise ValueError("Composer destinations are invalid")
        if any(
            not isinstance(value, (IPv4Network, IPv6Network))
            for value in self.allowed_networks
        ):
            raise ValueError("Composer address networks are invalid")
        limits = (
            self.maximum_request_bytes,
            self.maximum_response_bytes,
            self.maximum_models,
            self.maximum_model_name_length,
            self.maximum_credential_bytes,
            self.maximum_output_tokens,
            self.maximum_concurrent_calls,
            self.maximum_allowed_users,
        )
        if any(type(value) is not int or value <= 0 for value in limits):
            raise ValueError("Composer limits must be positive integers")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Composer timeout must be positive and finite")


@dataclass(frozen=True, slots=True)
class ValidatedEndpoint:
    host: str
    port: int
    base_path: str


@dataclass(frozen=True, slots=True)
class _ExchangePayload:
    credentials: PlainCredentials
    method: str
    suffix: str
    encoded: bytes
    policy: ConnectionPolicy
    timeout_seconds: float
    cancel_event: threading.Event | None


def validate_endpoint(url: str, policy: ConnectionPolicy) -> ValidatedEndpoint:
    """Require exact scheme, authority, path, host, and port policy."""

    if not url or url.strip() != url or any(ord(c) < _MIN_VISIBLE_ASCII for c in url):
        raise EgressPolicyError("Connection endpoint is invalid")
    try:
        parsed: SplitResult = urlsplit(url)
        port = 443 if parsed.port is None else parsed.port
    except ValueError:
        raise EgressPolicyError("Connection endpoint is invalid") from None
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host is None
        or host != host.lower()
        or not _HOST_RE.fullmatch(host)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.netloc
        or parsed.netloc.endswith(":")
        or not 1 <= port <= _MAX_PORT
        or AllowedDestination(host, port) not in policy.destinations
    ):
        raise EgressPolicyError("Connection endpoint is not allowed")
    path = parsed.path.rstrip("/")
    if path and (
        not _PATH_RE.fullmatch(path)
        or any(part in {".", ".."} for part in path.split("/"))
        or "//" in path
    ):
        raise EgressPolicyError("Connection endpoint path is invalid")
    return ValidatedEndpoint(host, port, path)


def _system_resolver(host: str, port: int) -> tuple[str, ...]:
    addresses: set[str] = set()
    for family, _, _, _, sockaddr in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    ):
        if family in (socket.AF_INET, socket.AF_INET6):
            addresses.add(str(sockaddr[0]))
    return tuple(sorted(addresses))


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        endpoint: ValidatedEndpoint,
        address: IPAddress,
        context: ssl.SSLContext,
        timeout: float,
        cancel_event: threading.Event | None,
    ) -> None:
        super().__init__(endpoint.host, endpoint.port, context=context, timeout=timeout)
        self._address = address
        self._tls_context = context
        self._cancel_event = cancel_event

    def connect(self) -> None:
        _raise_if_cancelled(self._cancel_event)
        plain = socket.create_connection(
            (str(self._address), self.port), timeout=self.timeout
        )
        try:
            self.sock = plain
            _raise_if_cancelled(self._cancel_event)
            secured = self._tls_context.wrap_socket(
                plain, server_hostname=self.host, do_handshake_on_connect=False
            )
            self.sock = secured
            secured.do_handshake()
            _raise_if_cancelled(self._cancel_event)
        except Exception:
            plain.close()
            raise


class ConnectionEgress:
    """One backend-only egress gate with independent DNS checks per request."""

    def __init__(
        self, policy: ConnectionPolicy, resolver: Resolver | None = None
    ) -> None:
        self.policy = policy
        self._resolver = resolver or _system_resolver
        self._slots = threading.BoundedSemaphore(policy.maximum_concurrent_calls)
        self._resolver_slots = threading.BoundedSemaphore(
            policy.maximum_concurrent_calls
        )

    def discover_models(
        self,
        endpoint_url: str,
        credentials: PlainCredentials,
        *,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, ...]:
        payload = self._request(
            endpoint_url,
            credentials,
            "GET",
            "/models",
            None,
            cancel_event=cancel_event,
        )
        entries = payload.get("data")
        if not isinstance(entries, list) or len(entries) > self.policy.maximum_models:
            raise EgressResponseError("Provider model list is invalid")
        models: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict) or not _valid_model(
                entry.get("id"), self.policy.maximum_model_name_length
            ):
                raise EgressResponseError("Provider model list is invalid")
            models.append(entry["id"])
        _raise_if_cancelled(cancel_event)
        return tuple(dict.fromkeys(models))

    def chat(  # noqa: PLR0913 - cancellation is an explicit call contract
        self,
        endpoint_url: str,
        credentials: PlainCredentials,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        _raise_if_cancelled(cancel_event)
        if not _valid_model(model, self.policy.maximum_model_name_length):
            raise EgressPolicyError("Selected model is invalid")
        if (
            type(max_output_tokens) is not int
            or not 1 <= max_output_tokens <= self.policy.maximum_output_tokens
        ):
            raise EgressPolicyError("Output token limit is invalid")
        if not messages or any(
            not isinstance(item, dict)
            or set(item) != {"role", "content"}
            or item["role"] not in {"system", "user", "assistant"}
            or not isinstance(item["content"], str)
            for item in messages
        ):
            raise EgressPolicyError("Model messages are invalid")
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "stream": False,
        }
        response = self._request(
            endpoint_url,
            credentials,
            "POST",
            "/chat/completions",
            body,
            cancel_event=cancel_event,
        )
        choices = response.get("choices")
        if (
            not isinstance(choices, list)
            or len(choices) != 1
            or not isinstance(choices[0], dict)
            or not isinstance(choices[0].get("message"), dict)
            or not isinstance(choices[0]["message"].get("content"), str)
        ):
            raise EgressResponseError("Provider completion is invalid")
        _raise_if_cancelled(cancel_event)
        return response

    def _request(  # noqa: PLR0913 - bounded request and cancellation parameters
        self,
        endpoint_url: str,
        credentials: PlainCredentials,
        method: str,
        suffix: str,
        body: dict[str, Any] | None,
        *,
        cancel_event: threading.Event | None,
    ) -> dict[str, Any]:
        _raise_if_cancelled(cancel_event)
        endpoint = validate_endpoint(endpoint_url, self.policy)
        try:
            encoded = (
                json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode(
                    "utf-8"
                )
                if body is not None
                else b""
            )
        except UnicodeEncodeError:
            raise EgressPolicyError("Model request is invalid") from None
        if len(encoded) > self.policy.maximum_request_bytes:
            raise EgressPolicyError("Model request exceeds its configured limit")
        _validate_credential_sizes(credentials, self.policy.maximum_credential_bytes)
        deadline = time.monotonic() + self.policy.timeout_seconds
        _raise_if_cancelled(cancel_event)
        if not self._slots.acquire(blocking=False):
            _raise_if_cancelled(cancel_event)
            raise EgressCapacityError("Model connection is busy")
        try:
            _raise_if_cancelled(cancel_event)
            address = self._resolve(
                endpoint,
                timeout=deadline - time.monotonic(),
                cancel_event=cancel_event,
            )
            _raise_if_cancelled(cancel_event)
            context = _tls_context(credentials)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EgressUnavailableError("Model provider timed out")
            raw = _exchange(
                endpoint,
                address,
                context,
                _ExchangePayload(
                    credentials,
                    method,
                    suffix,
                    encoded,
                    self.policy,
                    remaining,
                    cancel_event,
                ),
            )
        except OSError, ssl.SSLError, http.client.HTTPException:
            _raise_if_cancelled(cancel_event)
            raise EgressUnavailableError("Model provider is unavailable") from None
        except ValueError as error:
            _raise_if_cancelled(cancel_event)
            if isinstance(error, EgressPolicyError):
                raise
            raise EgressUnavailableError("Model provider is unavailable") from None
        finally:
            self._slots.release()
        _raise_if_cancelled(cancel_event)
        try:
            parsed = json.loads(raw)
        except UnicodeDecodeError, json.JSONDecodeError:
            raise EgressResponseError("Provider response is invalid") from None
        if not isinstance(parsed, dict):
            raise EgressResponseError("Provider response is invalid")
        _raise_if_cancelled(cancel_event)
        return parsed

    def _resolve(
        self,
        endpoint: ValidatedEndpoint,
        *,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> IPAddress:
        budget = self.policy.timeout_seconds if timeout is None else timeout
        deadline = time.monotonic() + budget
        if budget <= 0:
            raise EgressUnavailableError("Model destination resolution timed out")
        _raise_if_cancelled(cancel_event)
        if not self._resolver_slots.acquire(blocking=False):
            _raise_if_cancelled(cancel_event)
            raise EgressCapacityError("Model destination resolution is busy")
        if cancel_event is not None and cancel_event.is_set():
            self._resolver_slots.release()
            raise EgressCancelledError("Model operation was cancelled")
        result: queue.Queue[Sequence[str] | Exception] = queue.Queue(maxsize=1)

        def work() -> None:
            try:
                result.put(self._resolver(endpoint.host, endpoint.port))
            except Exception as error:
                result.put(error)
            finally:
                self._resolver_slots.release()

        try:
            threading.Thread(target=work, daemon=True).start()
        except RuntimeError:
            self._resolver_slots.release()
            raise EgressUnavailableError(
                "Model destination resolution failed"
            ) from None
        try:
            while True:
                _raise_if_cancelled(cancel_event)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                try:
                    names = result.get(timeout=min(remaining, _CANCEL_POLL_SECONDS))
                    break
                except queue.Empty:
                    continue
            if isinstance(names, Exception):
                raise names
            addresses = tuple(ipaddress.ip_address(value) for value in names)
        except EgressCancelledError:
            raise
        except Exception:
            raise EgressUnavailableError(
                "Model destination could not be resolved"
            ) from None
        if not addresses or not self.policy.allowed_networks:
            raise EgressPolicyError("Model destination address is not allowed")
        if any(
            not any(address in network for network in self.policy.allowed_networks)
            for address in addresses
        ):
            raise EgressPolicyError("Model destination address is not allowed")
        _raise_if_cancelled(cancel_event)
        return sorted(addresses, key=lambda address: (address.version, int(address)))[0]


def _exchange(
    endpoint: ValidatedEndpoint,
    address: IPAddress,
    context: ssl.SSLContext,
    payload: _ExchangePayload,
) -> bytes:
    headers = {"Accept": "application/json", "Connection": "close"}
    if payload.credentials.api_key is not None:
        try:
            key = payload.credentials.api_key.decode("ascii")
        except UnicodeDecodeError:
            raise EgressPolicyError("API credential is invalid") from None
        if any(
            ord(char) < _MIN_VISIBLE_ASCII or ord(char) > _MAX_VISIBLE_ASCII
            for char in key
        ):
            raise EgressPolicyError("API credential is invalid")
        headers["Authorization"] = f"Bearer {key}"
    if payload.method == "POST":
        headers["Content-Type"] = "application/json"
    connection = _PinnedHTTPSConnection(
        endpoint, address, context, payload.timeout_seconds, payload.cancel_event
    )
    deadline = time.monotonic() + payload.timeout_seconds
    timer = threading.Timer(payload.timeout_seconds, _abort_connection, (connection,))
    timer.daemon = True
    completed = threading.Event()
    try:
        if payload.cancel_event is not None:

            def abort_on_cancel() -> None:
                while not completed.is_set():
                    if payload.cancel_event is not None and payload.cancel_event.wait(
                        _CANCEL_POLL_SECONDS
                    ):
                        _abort_connection(connection)
                        return

            threading.Thread(target=abort_on_cancel, daemon=True).start()
        timer.start()
        _raise_if_cancelled(payload.cancel_event)
        connection.request(
            payload.method,
            endpoint.base_path + payload.suffix,
            payload.encoded,
            headers,
        )
        response = connection.getresponse()
        if _REDIRECT_MIN <= response.status < _REDIRECT_MAX:
            raise EgressResponseError("Provider redirect is not allowed")
        if response.status != _SUCCESS_STATUS:
            raise EgressUnavailableError("Model provider is unavailable")
        length = response.getheader("Content-Length")
        if length is not None and _content_length_exceeds(
            length, payload.policy.maximum_response_bytes
        ):
            raise EgressResponseError("Provider response exceeds its configured limit")
        raw = response.read(payload.policy.maximum_response_bytes + 1)
        _raise_if_cancelled(payload.cancel_event)
        if time.monotonic() > deadline:
            raise EgressUnavailableError("Model provider timed out")
        if len(raw) > payload.policy.maximum_response_bytes:
            raise EgressResponseError("Provider response exceeds its configured limit")
        return raw
    except RuntimeError:
        _raise_if_cancelled(payload.cancel_event)
        raise EgressUnavailableError("Model provider is unavailable") from None
    finally:
        completed.set()
        timer.cancel()
        connection.close()


def _abort_connection(connection: _PinnedHTTPSConnection) -> None:
    if connection.sock is not None:
        with suppress(OSError):
            connection.sock.shutdown(socket.SHUT_RDWR)
    connection.close()


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise EgressCancelledError("Model operation was cancelled")


def _content_length_exceeds(value: str, limit: int) -> bool:
    if not value.isdecimal():
        return True
    normalized = value.lstrip("0") or "0"
    maximum = str(limit)
    return len(normalized) > len(maximum) or (
        len(normalized) == len(maximum) and normalized > maximum
    )


def _valid_model(value: object, maximum_length: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum_length
        and value.strip() == value
        and all(_MIN_VISIBLE_ASCII <= ord(char) <= _MAX_VISIBLE_ASCII for char in value)
    )


def _validate_credential_sizes(credentials: PlainCredentials, limit: int) -> None:
    if any(
        value is not None and len(value) > limit
        for value in (
            credentials.api_key,
            credentials.client_certificate,
            credentials.client_private_key,
            credentials.ca_bundle,
        )
    ):
        raise EgressPolicyError("Connection credential exceeds its configured limit")


def _tls_context(credentials: PlainCredentials) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if credentials.ca_bundle is not None:
        try:
            context.load_verify_locations(cadata=credentials.ca_bundle.decode("ascii"))
        except UnicodeDecodeError, ssl.SSLError:
            raise EgressPolicyError("Trusted CA bundle is invalid") from None
    if credentials.client_certificate is not None:
        if credentials.client_private_key is None:
            raise EgressPolicyError("Client private key is missing")
        if not hasattr(os, "memfd_create"):
            raise EgressPolicyError(
                "In-memory client certificate loading is unavailable"
            )
        cert_fd = os.memfd_create("composer-cert", os.MFD_CLOEXEC)
        key_fd = os.memfd_create("composer-key", os.MFD_CLOEXEC)
        try:
            os.write(cert_fd, credentials.client_certificate)
            os.write(key_fd, credentials.client_private_key)
            try:
                context.load_cert_chain(
                    f"/proc/self/fd/{cert_fd}", f"/proc/self/fd/{key_fd}"
                )
            except OSError, ssl.SSLError:
                raise EgressPolicyError("Client certificate is invalid") from None
        finally:
            os.close(cert_fd)
            os.close(key_fd)
    return context
