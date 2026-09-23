"""Reject unsafe egress configuration before any network request."""

from __future__ import annotations

import os
import socket
import time
from dataclasses import replace
from ipaddress import ip_address, ip_network
from threading import Event, Thread

import pytest
from pytest_mock import MockerFixture

from markweave.composer.egress import (
    AllowedDestination,
    ConnectionEgress,
    ConnectionPolicy,
    EgressCancelledError,
    EgressCapacityError,
    EgressPolicyError,
    EgressResponseError,
    EgressUnavailableError,
    _system_resolver,
    _tls_context,
    validate_endpoint,
)
from markweave.composer.secrets import PlainCredentials

pytestmark = pytest.mark.unit


@pytest.fixture
def policy() -> ConnectionPolicy:
    return ConnectionPolicy(
        destinations=frozenset({AllowedDestination("models.example", 443)}),
        allowed_networks=(ip_network("203.0.113.0/24"),),
        maximum_request_bytes=1024,
        maximum_response_bytes=1024,
        maximum_models=2,
        maximum_model_name_length=20,
        maximum_credential_bytes=1024,
        maximum_output_tokens=8,
        maximum_concurrent_calls=1,
        maximum_allowed_users=10,
        timeout_seconds=0.05,
    )


@pytest.mark.parametrize(
    ("host", "port"),
    [("UPPER.example", 443), ("models.example", 0), ("bad_host", 443)],
)
def test_destination_rejects_invalid_authority(host: str, port: int) -> None:
    with pytest.raises(ValueError):
        AllowedDestination(host, port)


def test_policy_requires_typed_positive_budgets(policy: ConnectionPolicy) -> None:
    for change in (
        {"destinations": frozenset({"models.example:443"})},
        {"allowed_networks": ("0.0.0.0/0",)},
        {"maximum_request_bytes": 0},
        {"maximum_concurrent_calls": True},
        {"timeout_seconds": float("inf")},
    ):
        with pytest.raises(ValueError):
            replace(policy, **change)


@pytest.mark.parametrize(
    "url",
    [
        "",
        " https://models.example/v1",
        "https://models.example:abc/v1",
        "https://models.example/v1\n",
    ],
)
def test_endpoint_rejects_malformed_url(url: str, policy: ConnectionPolicy) -> None:
    with pytest.raises(EgressPolicyError):
        validate_endpoint(url, policy)


def test_system_dns_collects_only_ip_addresses(mocker: MockerFixture) -> None:
    mocker.patch(
        "markweave.composer.egress.socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, 0, 0, "", ("203.0.113.2", 443)),
            (socket.AF_INET6, 0, 0, "", ("2001:db8::1", 443, 0, 0)),
            (socket.AF_UNIX, 0, 0, "", "ignored-unix-socket"),
        ],
    )
    assert _system_resolver("models.example", 443) == (
        "2001:db8::1",
        "203.0.113.2",
    )


def test_request_and_credential_limits_fail_before_dns(
    policy: ConnectionPolicy,
) -> None:
    egress = ConnectionEgress(replace(policy, maximum_request_bytes=10))
    with pytest.raises(EgressPolicyError, match="request exceeds"):
        egress.chat(
            "https://models.example/v1",
            PlainCredentials(api_key=b"x"),
            model="model",
            messages=[{"role": "user", "content": "Hi"}],
            max_output_tokens=1,
        )
    egress = ConnectionEgress(replace(policy, maximum_credential_bytes=1))
    with pytest.raises(EgressPolicyError, match="credential exceeds"):
        egress.discover_models(
            "https://models.example/v1", PlainCredentials(api_key=b"long")
        )


def test_invalid_unicode_message_is_safe_and_never_resolves(
    policy: ConnectionPolicy,
) -> None:
    calls = 0

    def resolve(_host: str, _port: int) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        return ("203.0.113.1",)

    egress = ConnectionEgress(policy, resolve)
    with pytest.raises(EgressPolicyError, match="Model request is invalid"):
        egress.chat(
            "https://models.example/v1",
            PlainCredentials(api_key=b"key"),
            model="model",
            messages=[{"role": "user", "content": "\ud800"}],
            max_output_tokens=1,
        )
    assert calls == 0


def test_cancellation_before_dns_and_during_resolution_releases_prompt_slot(
    policy: ConnectionPolicy,
) -> None:
    started = Event()
    finish_dns = Event()
    cancelled = Event()
    calls = 0

    def resolve(_host: str, _port: int) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        started.set()
        finish_dns.wait(timeout=2)
        return ("203.0.113.1",)

    egress = ConnectionEgress(policy, resolve)
    cancelled.set()
    with pytest.raises(EgressCancelledError):
        egress.discover_models(
            "https://models.example/v1",
            PlainCredentials(api_key=b"key"),
            cancel_event=cancelled,
        )
    assert calls == 0

    cancelled.clear()
    outcomes: list[object] = []

    def call() -> None:
        try:
            outcomes.append(
                egress.discover_models(
                    "https://models.example/v1",
                    PlainCredentials(api_key=b"key"),
                    cancel_event=cancelled,
                )
            )
        except Exception as error:
            outcomes.append(error)

    worker = Thread(target=call)
    worker.start()
    try:
        assert started.wait(timeout=1)
        cancelled.set()
        worker.join(timeout=1)
        assert not worker.is_alive()
        assert len(outcomes) == 1
        assert isinstance(outcomes[0], EgressCancelledError)
        assert egress._slots.acquire(blocking=False)
        egress._slots.release()
        assert not egress._resolver_slots.acquire(blocking=False)
    finally:
        finish_dns.set()
        worker.join(timeout=2)


def test_busy_slot_and_invalid_json_result(
    policy: ConnectionPolicy, mocker: MockerFixture
) -> None:
    egress = ConnectionEgress(policy, lambda _host, _port: ("203.0.113.1",))
    assert egress._slots.acquire(blocking=False)
    try:
        with pytest.raises(EgressCapacityError, match="busy"):
            egress.discover_models(
                "https://models.example/v1", PlainCredentials(api_key=b"x")
            )
    finally:
        egress._slots.release()
    mocker.patch.object(egress, "_resolve", return_value=ip_address("203.0.113.1"))
    exchange = mocker.patch("markweave.composer.egress._exchange", return_value=b"[]")
    with pytest.raises(EgressResponseError, match="invalid"):
        egress.discover_models(
            "https://models.example/v1", PlainCredentials(api_key=b"x")
        )
    exchange.assert_called_once()


def test_dns_capacity_is_distinct_from_provider_outage(
    policy: ConnectionPolicy,
) -> None:
    calls = 0

    def resolve(_host: str, _port: int) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        return ("203.0.113.1",)

    egress = ConnectionEgress(replace(policy, timeout_seconds=2), resolve)
    assert egress._resolver_slots.acquire(blocking=False)
    try:
        started_at = time.monotonic()
        with pytest.raises(EgressCapacityError, match="resolution is busy"):
            egress._resolve(validate_endpoint("https://models.example/v1", policy))
        assert time.monotonic() - started_at < 0.5
    finally:
        egress._resolver_slots.release()
    assert calls == 0


def test_dns_zero_budget_and_expired_pre_exchange_deadline_fail_closed(
    policy: ConnectionPolicy, mocker: MockerFixture
) -> None:
    calls = 0

    def resolve(_host: str, _port: int) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        return ("203.0.113.1",)

    egress = ConnectionEgress(policy, resolve)
    endpoint = validate_endpoint("https://models.example/v1", policy)
    with pytest.raises(EgressUnavailableError, match="resolution timed out"):
        egress._resolve(endpoint, timeout=0)
    assert calls == 0

    bounded = ConnectionEgress(replace(policy, timeout_seconds=0.001), resolve)

    def late_address(*_args: object, **_kwargs: object) -> object:
        time.sleep(0.01)
        return ip_address("203.0.113.1")

    mocker.patch.object(bounded, "_resolve", side_effect=late_address)
    exchange = mocker.patch("markweave.composer.egress._exchange")
    with pytest.raises(EgressUnavailableError, match="provider timed out"):
        bounded.discover_models(
            "https://models.example/v1", PlainCredentials(api_key=b"key")
        )
    exchange.assert_not_called()


def test_transport_value_error_is_safe_unavailable(
    policy: ConnectionPolicy, mocker: MockerFixture
) -> None:
    egress = ConnectionEgress(policy, lambda _host, _port: ("203.0.113.1",))
    mocker.patch.object(egress, "_resolve", return_value=ip_address("203.0.113.1"))
    mocker.patch(
        "markweave.composer.egress._exchange",
        side_effect=ValueError("private transport path"),
    )
    with pytest.raises(EgressUnavailableError) as error:
        egress.discover_models(
            "https://models.example/v1", PlainCredentials(api_key=b"key")
        )
    assert "private transport path" not in str(error.value)


def test_dns_resolver_failures_are_safe(
    policy: ConnectionPolicy, mocker: MockerFixture
) -> None:
    def fail(_host: str, _port: int) -> tuple[str, ...]:
        raise OSError("private resolver detail")

    egress = ConnectionEgress(policy, fail)
    endpoint = validate_endpoint("https://models.example/v1", policy)
    with pytest.raises(EgressUnavailableError) as error:
        egress._resolve(endpoint)
    assert "private resolver detail" not in str(error.value)
    start = mocker.patch("markweave.composer.egress.threading.Thread.start")
    start.side_effect = RuntimeError("cannot start")
    with pytest.raises(EgressUnavailableError, match="resolution failed"):
        egress._resolve(endpoint)


def test_bad_tls_material_and_header_are_rejected(
    policy: ConnectionPolicy, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(EgressPolicyError, match="Trusted CA"):
        _tls_context(PlainCredentials(api_key=b"x", ca_bundle=b"not a CA"))
    with pytest.raises(EgressPolicyError, match="Client certificate"):
        _tls_context(
            PlainCredentials(
                client_certificate=b"not a certificate",
                client_private_key=b"not a key",
            )
        )
    monkeypatch.delattr(os, "memfd_create")
    with pytest.raises(EgressPolicyError, match="In-memory"):
        _tls_context(
            PlainCredentials(client_certificate=b"cert", client_private_key=b"key")
        )
    egress = ConnectionEgress(policy, lambda _host, _port: ("203.0.113.1",))
    mocker.patch.object(egress, "_resolve", return_value=ip_address("203.0.113.1"))
    with pytest.raises(EgressPolicyError, match="API credential"):
        egress.discover_models(
            "https://models.example/v1", PlainCredentials(api_key=b"\xff")
        )
    with pytest.raises(EgressPolicyError, match="API credential"):
        egress.discover_models(
            "https://models.example/v1", PlainCredentials(api_key=b"bad key")
        )
