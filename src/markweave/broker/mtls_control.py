"""Canonical bounded control frames for the paired-channel mTLS protocol.

Socket deadlines, TLS authentication, and dispatch remain transport responsibilities.
"""

from __future__ import annotations

import hashlib
import json
from typing import Final
from uuid import UUID

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.protocol import LENGTH_PREFIX_BYTES

MTLS_PROTOCOL_NAME: Final = "markweave-reverse-broker-mtls"
MTLS_PROTOCOL_VERSION: Final = 1
_CONTROL_PAYLOAD_MAX: Final = 4096
_DIGEST_PREFIX: Final = "sha256:"
_PIN_LENGTH: Final = len(_DIGEST_PREFIX) + 64
_EXCHANGE_HEX_LENGTH: Final = 64
_RESERVATION_KEYS = {
    "channel",
    "operation",
    "protocol",
    "request_id",
    "version",
}
_SUBMIT_KEYS = _RESERVATION_KEYS | {
    "exchange_id",
    "frame_length",
    "frame_sha256",
}
_ACK_KEYS = {"exchange_id", "operation", "protocol", "request_id", "version"}
_RESPONSE_KEYS = _ACK_KEYS | {"frame_length", "frame_sha256"}


def _valid_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == _PIN_LENGTH
        and value.startswith(_DIGEST_PREFIX)
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _canonical_json(value: dict[str, object]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR) from error
    if not encoded or len(encoded) > _CONTROL_PAYLOAD_MAX:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return encoded


def _control_frame(value: dict[str, object]) -> bytes:
    payload = _canonical_json(value)
    return len(payload).to_bytes(LENGTH_PREFIX_BYTES, "big") + payload


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _decode_control(
    frame: bytes, expected: set[str] | tuple[set[str], ...]
) -> dict[str, object]:
    if type(frame) is not bytes or len(frame) < LENGTH_PREFIX_BYTES:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    length = int.from_bytes(frame[:LENGTH_PREFIX_BYTES], "big")
    payload = frame[LENGTH_PREFIX_BYTES:]
    if length != len(payload) or not 0 < length <= _CONTROL_PAYLOAD_MAX:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR) from error
    expected_schemas = (expected,) if type(expected) is set else expected
    if (
        type(value) is not dict
        or not any(set(value) == schema for schema in expected_schemas)
        or value.get("protocol") != MTLS_PROTOCOL_NAME
        or value.get("version") != MTLS_PROTOCOL_VERSION
        or _canonical_json(value) != payload
    ):
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return value


def _exchange_id(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != _EXCHANGE_HEX_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return value


def _uuid(value: object) -> UUID:
    if type(value) is not str:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR) from error
    if str(parsed) != value:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return parsed


def _positive_length(value: object, maximum: int) -> int:
    if type(value) is not int or not 0 < value <= maximum:
        raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)
    return value


def _reservation_mapping(request_id: UUID) -> dict[str, object]:
    return {
        "channel": "response",
        "operation": "RESERVE",
        "protocol": MTLS_PROTOCOL_NAME,
        "request_id": str(request_id),
        "version": MTLS_PROTOCOL_VERSION,
    }


def _submit_mapping(exchange: str, request_id: UUID, frame: bytes) -> dict[str, object]:
    return {
        "channel": "request",
        "exchange_id": exchange,
        "frame_length": len(frame),
        "frame_sha256": _sha256(frame),
        "operation": "SUBMIT",
        "protocol": MTLS_PROTOCOL_NAME,
        "request_id": str(request_id),
        "version": MTLS_PROTOCOL_VERSION,
    }


def _ack_mapping(exchange: str, request_id: UUID) -> dict[str, object]:
    return {
        "exchange_id": exchange,
        "operation": "RESERVED",
        "protocol": MTLS_PROTOCOL_NAME,
        "request_id": str(request_id),
        "version": MTLS_PROTOCOL_VERSION,
    }


def _response_mapping(
    exchange: str, request_id: UUID, frame: bytes
) -> dict[str, object]:
    return {
        "exchange_id": exchange,
        "frame_length": len(frame),
        "frame_sha256": _sha256(frame),
        "operation": "RESPONSE",
        "protocol": MTLS_PROTOCOL_NAME,
        "request_id": str(request_id),
        "version": MTLS_PROTOCOL_VERSION,
    }
