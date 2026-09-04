"""Canonical bounded control protocol for the reverse-isolation broker."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Never
from uuid import UUID

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import (
    MAX_SEQUENCE,
    AuthenticatedPrincipal,
    EvidenceDigest,
    ManagedUnitState,
    TerminationProof,
)

PROTOCOL_NAME = "markweave-reverse-broker"
PROTOCOL_VERSION = 1
LENGTH_PREFIX_BYTES = 4
MAX_FRAME_BYTES = 4096


class BrokerOperation(StrEnum):
    """The complete closed set of worker-visible broker operations."""

    CREATE = "create"
    STATUS = "status"
    TERMINATE = "terminate"
    PROOF = "proof"
    ACK = "ack"
    READY = "ready"


@dataclass(frozen=True, slots=True)
class CreateRequest:
    request_id: UUID
    sequence: int
    attempt_id: UUID


@dataclass(frozen=True, slots=True)
class StatusRequest:
    request_id: UUID
    sequence: int
    attempt_id: UUID
    unit_id: UUID


@dataclass(frozen=True, slots=True)
class TerminateRequest:
    request_id: UUID
    sequence: int
    attempt_id: UUID
    unit_id: UUID


@dataclass(frozen=True, slots=True)
class ProofRequest:
    request_id: UUID
    sequence: int
    attempt_id: UUID
    unit_id: UUID


@dataclass(frozen=True, slots=True)
class AcknowledgeRequest:
    request_id: UUID
    sequence: int
    attempt_id: UUID
    unit_id: UUID
    proof_id: UUID


@dataclass(frozen=True, slots=True)
class ReadyRequest:
    request_id: UUID
    sequence: int


BrokerRequest = (
    CreateRequest
    | StatusRequest
    | TerminateRequest
    | ProofRequest
    | AcknowledgeRequest
    | ReadyRequest
)


@dataclass(frozen=True, slots=True)
class CreateResponse:
    request_id: UUID
    attempt_id: UUID
    unit_id: UUID
    state: ManagedUnitState


@dataclass(frozen=True, slots=True)
class StatusResponse:
    request_id: UUID
    attempt_id: UUID
    unit_id: UUID
    state: ManagedUnitState


@dataclass(frozen=True, slots=True)
class TerminateResponse:
    request_id: UUID
    attempt_id: UUID
    unit_id: UUID
    state: ManagedUnitState


@dataclass(frozen=True, slots=True)
class ProofResponse:
    request_id: UUID
    proof: TerminationProof


@dataclass(frozen=True, slots=True)
class AcknowledgeResponse:
    request_id: UUID
    attempt_id: UUID
    unit_id: UUID
    proof_id: UUID


@dataclass(frozen=True, slots=True)
class ReadyResponse:
    request_id: UUID
    ready: bool


@dataclass(frozen=True, slots=True)
class ErrorResponse:
    request_id: UUID
    operation: BrokerOperation
    category: BrokerErrorCategory


BrokerResponse = (
    CreateResponse
    | StatusResponse
    | TerminateResponse
    | ProofResponse
    | AcknowledgeResponse
    | ReadyResponse
    | ErrorResponse
)

_COMMON_KEYS = frozenset({"operation", "protocol", "request_id", "sequence", "version"})
_ATTEMPT_KEYS = _COMMON_KEYS | {"attempt_id", "unit_id"}
_SCHEMAS: dict[BrokerOperation, frozenset[str]] = {
    BrokerOperation.CREATE: _COMMON_KEYS | {"attempt_id"},
    BrokerOperation.STATUS: _ATTEMPT_KEYS,
    BrokerOperation.TERMINATE: _ATTEMPT_KEYS,
    BrokerOperation.PROOF: _ATTEMPT_KEYS,
    BrokerOperation.ACK: _ATTEMPT_KEYS | {"proof_id"},
    BrokerOperation.READY: _COMMON_KEYS,
}
_RESPONSE_COMMON_KEYS = frozenset(
    {"operation", "outcome", "protocol", "request_id", "version"}
)
_UNIT_RESPONSE_KEYS = _RESPONSE_COMMON_KEYS | {"attempt_id", "state", "unit_id"}
_PROOF_RESPONSE_KEYS = _RESPONSE_COMMON_KEYS | {
    "attempt_id",
    "empty_evidence",
    "exit_evidence",
    "policy_revision",
    "principal_id",
    "proof_id",
    "removal_evidence",
    "unit_id",
}


def _fail() -> Never:
    raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)


def _canonical_json(value: dict[str, object]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except TypeError, ValueError, UnicodeEncodeError:
        _fail()
    if not encoded or len(encoded) > MAX_FRAME_BYTES:
        _fail()
    return encoded


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def decode_length_prefix(prefix: bytes) -> int:
    """Validate a frame size before callers allocate or read its payload."""

    if type(prefix) is not bytes or len(prefix) != LENGTH_PREFIX_BYTES:
        _fail()
    length = int.from_bytes(prefix, "big")
    if not 0 < length <= MAX_FRAME_BYTES:
        _fail()
    return length


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        _fail()
    try:
        parsed = UUID(value)
    except ValueError:
        _fail()
    if str(parsed) != value:
        _fail()
    return parsed


def _sequence(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_SEQUENCE:
        _fail()
    return value


def _request_mapping(request: BrokerRequest) -> dict[str, object]:
    if type(request) not in {
        CreateRequest,
        StatusRequest,
        TerminateRequest,
        ProofRequest,
        AcknowledgeRequest,
        ReadyRequest,
    }:
        _fail()
    if type(request.request_id) is not UUID:
        _fail()
    base: dict[str, object] = {
        "protocol": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
        "request_id": str(request.request_id),
        "sequence": _sequence(request.sequence),
    }
    match request:
        case CreateRequest(attempt_id=attempt_id):
            operation = BrokerOperation.CREATE
            base["attempt_id"] = str(_checked_uuid(attempt_id))
        case StatusRequest(attempt_id=attempt_id, unit_id=unit_id):
            operation = BrokerOperation.STATUS
            base.update(
                attempt_id=str(_checked_uuid(attempt_id)),
                unit_id=str(_checked_uuid(unit_id)),
            )
        case TerminateRequest(attempt_id=attempt_id, unit_id=unit_id):
            operation = BrokerOperation.TERMINATE
            base.update(
                attempt_id=str(_checked_uuid(attempt_id)),
                unit_id=str(_checked_uuid(unit_id)),
            )
        case ProofRequest(attempt_id=attempt_id, unit_id=unit_id):
            operation = BrokerOperation.PROOF
            base.update(
                attempt_id=str(_checked_uuid(attempt_id)),
                unit_id=str(_checked_uuid(unit_id)),
            )
        case AcknowledgeRequest(
            attempt_id=attempt_id, unit_id=unit_id, proof_id=proof_id
        ):
            operation = BrokerOperation.ACK
            base.update(
                attempt_id=str(_checked_uuid(attempt_id)),
                unit_id=str(_checked_uuid(unit_id)),
                proof_id=str(_checked_uuid(proof_id)),
            )
        case ReadyRequest():
            operation = BrokerOperation.READY
        case _:
            _fail()
    base["operation"] = operation
    return base


def _checked_uuid(value: object) -> UUID:
    if type(value) is not UUID:
        _fail()
    return value


def encode_request(request: BrokerRequest) -> bytes:
    """Encode one request with a four-byte network-order length prefix."""

    payload = _canonical_json(_request_mapping(request))
    return len(payload).to_bytes(LENGTH_PREFIX_BYTES, "big") + payload


def _frame(value: dict[str, object]) -> bytes:
    payload = _canonical_json(value)
    return len(payload).to_bytes(LENGTH_PREFIX_BYTES, "big") + payload


def decode_request(frame: bytes) -> BrokerRequest:
    """Decode exactly one complete canonical request frame."""

    if type(frame) is not bytes or len(frame) < LENGTH_PREFIX_BYTES:
        _fail()
    length = decode_length_prefix(frame[:LENGTH_PREFIX_BYTES])
    if len(frame) != LENGTH_PREFIX_BYTES + length:
        _fail()
    payload = frame[LENGTH_PREFIX_BYTES:]
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except UnicodeDecodeError, ValueError, json.JSONDecodeError:
        _fail()
    if not isinstance(value, dict) or _canonical_json(value) != payload:
        _fail()
    if (
        value.get("protocol") != PROTOCOL_NAME
        or type(value.get("version")) is not int
        or value.get("version") != PROTOCOL_VERSION
    ):
        _fail()
    operation_value = value.get("operation")
    if not isinstance(operation_value, str):
        _fail()
    try:
        operation = BrokerOperation(operation_value)
    except ValueError:
        _fail()
    if value.keys() != _SCHEMAS[operation]:
        _fail()
    return _REQUEST_DECODERS[operation](value)


def _common(value: dict[str, object]) -> tuple[UUID, int]:
    return _uuid(value["request_id"]), _sequence(value["sequence"])


def _decode_create(value: dict[str, object]) -> BrokerRequest:
    request_id, sequence = _common(value)
    return CreateRequest(request_id, sequence, _uuid(value["attempt_id"]))


def _attempt_unit(value: dict[str, object]) -> tuple[UUID, int, UUID, UUID]:
    request_id, sequence = _common(value)
    return (
        request_id,
        sequence,
        _uuid(value["attempt_id"]),
        _uuid(value["unit_id"]),
    )


def _decode_status(value: dict[str, object]) -> BrokerRequest:
    return StatusRequest(*_attempt_unit(value))


def _decode_terminate(value: dict[str, object]) -> BrokerRequest:
    return TerminateRequest(*_attempt_unit(value))


def _decode_proof(value: dict[str, object]) -> BrokerRequest:
    return ProofRequest(*_attempt_unit(value))


def _decode_ack(value: dict[str, object]) -> BrokerRequest:
    return AcknowledgeRequest(*_attempt_unit(value), _uuid(value["proof_id"]))


def _decode_ready(value: dict[str, object]) -> BrokerRequest:
    return ReadyRequest(*_common(value))


_REQUEST_DECODERS = {
    BrokerOperation.CREATE: _decode_create,
    BrokerOperation.STATUS: _decode_status,
    BrokerOperation.TERMINATE: _decode_terminate,
    BrokerOperation.PROOF: _decode_proof,
    BrokerOperation.ACK: _decode_ack,
    BrokerOperation.READY: _decode_ready,
}


def _response_mapping(response: BrokerResponse) -> dict[str, object]:
    if type(response) not in {
        CreateResponse,
        StatusResponse,
        TerminateResponse,
        ProofResponse,
        AcknowledgeResponse,
        ReadyResponse,
        ErrorResponse,
    }:
        _fail()
    request_id = str(_checked_uuid(response.request_id))
    base: dict[str, object] = {
        "outcome": "ok",
        "protocol": PROTOCOL_NAME,
        "request_id": request_id,
        "version": PROTOCOL_VERSION,
    }
    match response:
        case CreateResponse(attempt_id=attempt_id, unit_id=unit_id, state=state):
            operation = BrokerOperation.CREATE
            _add_unit_response(base, attempt_id, unit_id, state)
        case StatusResponse(attempt_id=attempt_id, unit_id=unit_id, state=state):
            operation = BrokerOperation.STATUS
            _add_unit_response(base, attempt_id, unit_id, state)
        case TerminateResponse(attempt_id=attempt_id, unit_id=unit_id, state=state):
            operation = BrokerOperation.TERMINATE
            _add_unit_response(base, attempt_id, unit_id, state)
        case ProofResponse(proof=proof):
            operation = BrokerOperation.PROOF
            _add_proof_response(base, proof)
        case AcknowledgeResponse(
            attempt_id=attempt_id, unit_id=unit_id, proof_id=proof_id
        ):
            operation = BrokerOperation.ACK
            base.update(
                attempt_id=str(_checked_uuid(attempt_id)),
                unit_id=str(_checked_uuid(unit_id)),
                proof_id=str(_checked_uuid(proof_id)),
            )
        case ReadyResponse(ready=ready):
            operation = BrokerOperation.READY
            if type(ready) is not bool:
                _fail()
            base["ready"] = ready
        case ErrorResponse(operation=operation, category=category):
            if (
                type(operation) is not BrokerOperation
                or type(category) is not BrokerErrorCategory
            ):
                _fail()
            base.update(outcome="error", category=category)
        case _:
            _fail()
    base["operation"] = operation
    return base


def _add_unit_response(
    value: dict[str, object],
    attempt_id: object,
    unit_id: object,
    state: object,
) -> None:
    if type(state) is not ManagedUnitState:
        _fail()
    value.update(
        attempt_id=str(_checked_uuid(attempt_id)),
        unit_id=str(_checked_uuid(unit_id)),
        state=state,
    )


def _add_proof_response(value: dict[str, object], proof: object) -> None:
    if type(proof) is not TerminationProof:
        _fail()
    value.update(
        proof_id=str(proof.proof_id),
        attempt_id=str(proof.attempt_id),
        unit_id=str(proof.unit_id),
        principal_id=str(proof.principal.principal_id),
        policy_revision=proof.policy_revision,
        exit_evidence=proof.exit_evidence.value,
        empty_evidence=proof.empty_evidence.value,
        removal_evidence=proof.removal_evidence.value,
    )


def encode_response(response: BrokerResponse) -> bytes:
    """Encode one bounded content-free broker response."""

    return _frame(_response_mapping(response))


def _decode_json_frame(frame: bytes) -> dict[str, object]:
    if type(frame) is not bytes or len(frame) < LENGTH_PREFIX_BYTES:
        _fail()
    length = decode_length_prefix(frame[:LENGTH_PREFIX_BYTES])
    if len(frame) != LENGTH_PREFIX_BYTES + length:
        _fail()
    payload = frame[LENGTH_PREFIX_BYTES:]
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except UnicodeDecodeError, ValueError, json.JSONDecodeError:
        _fail()
    if not isinstance(value, dict) or _canonical_json(value) != payload:
        _fail()
    if (
        value.get("protocol") != PROTOCOL_NAME
        or type(value.get("version")) is not int
        or value.get("version") != PROTOCOL_VERSION
    ):
        _fail()
    return value


def decode_response(frame: bytes) -> BrokerResponse:
    """Decode exactly one complete canonical content-free response frame."""

    value = _decode_json_frame(frame)
    operation_value = value.get("operation")
    outcome = value.get("outcome")
    if not isinstance(operation_value, str) or not isinstance(outcome, str):
        _fail()
    try:
        operation = BrokerOperation(operation_value)
    except ValueError:
        _fail()
    request_id = _uuid(value.get("request_id"))
    if outcome == "error":
        if value.keys() != _RESPONSE_COMMON_KEYS | {"category"}:
            _fail()
        category_value = value.get("category")
        if not isinstance(category_value, str):
            _fail()
        try:
            category = BrokerErrorCategory(category_value)
        except ValueError:
            _fail()
        return ErrorResponse(request_id, operation, category)
    if outcome != "ok":
        _fail()
    return _decode_success_response(value, operation, request_id)


def _decode_success_response(
    value: dict[str, object], operation: BrokerOperation, request_id: UUID
) -> BrokerResponse:
    if operation in {
        BrokerOperation.CREATE,
        BrokerOperation.STATUS,
        BrokerOperation.TERMINATE,
    }:
        if value.keys() != _UNIT_RESPONSE_KEYS:
            _fail()
        arguments = (
            request_id,
            _uuid(value.get("attempt_id")),
            _uuid(value.get("unit_id")),
            _state(value.get("state")),
        )
        constructors = {
            BrokerOperation.CREATE: CreateResponse,
            BrokerOperation.STATUS: StatusResponse,
            BrokerOperation.TERMINATE: TerminateResponse,
        }
        return constructors[operation](*arguments)
    if operation is BrokerOperation.PROOF:
        if value.keys() != _PROOF_RESPONSE_KEYS:
            _fail()
        try:
            proof = TerminationProof(
                _uuid(value.get("proof_id")),
                _uuid(value.get("attempt_id")),
                _uuid(value.get("unit_id")),
                AuthenticatedPrincipal(_uuid(value.get("principal_id"))),
                _policy_revision(value.get("policy_revision")),
                EvidenceDigest(_evidence(value.get("exit_evidence"))),
                EvidenceDigest(_evidence(value.get("empty_evidence"))),
                EvidenceDigest(_evidence(value.get("removal_evidence"))),
            )
        except ValueError:
            _fail()
        return ProofResponse(request_id, proof)
    if operation is BrokerOperation.ACK:
        if value.keys() != _RESPONSE_COMMON_KEYS | {
            "attempt_id",
            "proof_id",
            "unit_id",
        }:
            _fail()
        return AcknowledgeResponse(
            request_id,
            _uuid(value.get("attempt_id")),
            _uuid(value.get("unit_id")),
            _uuid(value.get("proof_id")),
        )
    if operation is BrokerOperation.READY:
        if value.keys() != _RESPONSE_COMMON_KEYS | {"ready"}:
            _fail()
        ready = value.get("ready")
        if type(ready) is not bool:
            _fail()
        return ReadyResponse(request_id, ready)
    return _fail()


def _state(value: object) -> ManagedUnitState:
    if not isinstance(value, str):
        _fail()
    try:
        return ManagedUnitState(value)
    except ValueError:
        _fail()


def _policy_revision(value: object) -> str:
    if not isinstance(value, str):
        _fail()
    return value


def _evidence(value: object) -> str:
    if not isinstance(value, str):
        _fail()
    return value
