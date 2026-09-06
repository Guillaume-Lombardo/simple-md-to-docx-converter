"""Closed authenticated protocol for broker inventory reconciliation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Never
from uuid import UUID

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import (
    MAX_SEQUENCE,
    AuthenticatedPrincipal,
    EvidenceDigest,
    TerminationProof,
)

PROTOCOL_NAME = "markweave-reverse-broker-reconciliation"
PROTOCOL_VERSION = 1
LENGTH_PREFIX_BYTES = 4
MAX_FRAME_BYTES = 4096


@dataclass(frozen=True, slots=True)
class ReconciliationRequest:
    request_id: UUID
    after_create_sequence: int


@dataclass(frozen=True, slots=True)
class ReconciliationTombstone:
    create_sequence: int
    policy_specification: EvidenceDigest
    proof: TerminationProof

    def __post_init__(self) -> None:
        if (
            type(self.create_sequence) is not int
            or not 1 <= self.create_sequence <= MAX_SEQUENCE
        ):
            raise ValueError("Reconciliation sequence is invalid")
        if type(self.policy_specification) is not EvidenceDigest:
            raise ValueError("Reconciliation policy specification is invalid")


@dataclass(frozen=True, slots=True)
class ReconciliationResponse:
    request_id: UUID
    principal_id: UUID
    after_create_sequence: int
    create_sequence_high_water: int
    tombstone: ReconciliationTombstone | None
    done: bool

    def __post_init__(self) -> None:
        if type(self.request_id) is not UUID or type(self.principal_id) is not UUID:
            raise ValueError("Reconciliation identity is invalid")
        if (
            type(self.after_create_sequence) is not int
            or not 0 <= self.after_create_sequence <= MAX_SEQUENCE
        ):
            raise ValueError("Reconciliation cursor is invalid")
        if (
            type(self.create_sequence_high_water) is not int
            or not 0 <= self.create_sequence_high_water <= MAX_SEQUENCE
            or self.create_sequence_high_water < self.after_create_sequence
        ):
            raise ValueError("Reconciliation high-water mark is invalid")
        if self.tombstone is not None and (
            type(self.tombstone) is not ReconciliationTombstone
            or self.tombstone.create_sequence > self.create_sequence_high_water
            or self.tombstone.create_sequence <= self.after_create_sequence
            or self.tombstone.proof.principal.principal_id != self.principal_id
        ):
            raise ValueError("Reconciliation tombstone is invalid")
        if type(self.done) is not bool or self.done is (self.tombstone is not None):
            raise ValueError("Reconciliation completion flag is invalid")


@dataclass(frozen=True, slots=True)
class ReconciliationErrorResponse:
    request_id: UUID
    category: BrokerErrorCategory


ReconciliationResult = ReconciliationResponse | ReconciliationErrorResponse


def _fail() -> Never:
    raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)


def _canonical(value: dict[str, object]) -> bytes:
    try:
        payload = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except TypeError, ValueError, UnicodeEncodeError, RecursionError:
        _fail()
    if not payload or len(payload) > MAX_FRAME_BYTES:
        _fail()
    return payload


def _frame(value: dict[str, object]) -> bytes:
    payload = _canonical(value)
    return len(payload).to_bytes(LENGTH_PREFIX_BYTES, "big") + payload


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


def _sequence(value: object, *, allow_zero: bool) -> int:
    minimum = 0 if allow_zero else 1
    if type(value) is not int or not minimum <= value <= MAX_SEQUENCE:
        _fail()
    return value


def _decode(frame: bytes) -> dict[str, object]:
    if type(frame) is not bytes or len(frame) < LENGTH_PREFIX_BYTES:
        _fail()
    length = int.from_bytes(frame[:LENGTH_PREFIX_BYTES], "big")
    if not 0 < length <= MAX_FRAME_BYTES or len(frame) != LENGTH_PREFIX_BYTES + length:
        _fail()
    payload = frame[LENGTH_PREFIX_BYTES:]
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except UnicodeDecodeError, ValueError, RecursionError:
        _fail()
    if not isinstance(value, dict) or _canonical(value) != payload:
        _fail()
    if (
        value.get("protocol") != PROTOCOL_NAME
        or value.get("version") != PROTOCOL_VERSION
    ):
        _fail()
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def encode_request(request: ReconciliationRequest) -> bytes:
    if (
        type(request) is not ReconciliationRequest
        or type(request.request_id) is not UUID
    ):
        _fail()
    return _frame(
        {
            "after_create_sequence": _sequence(
                request.after_create_sequence, allow_zero=True
            ),
            "operation": "query",
            "protocol": PROTOCOL_NAME,
            "request_id": str(request.request_id),
            "version": PROTOCOL_VERSION,
        }
    )


def decode_request(frame: bytes) -> ReconciliationRequest:
    value = _decode(frame)
    if (
        value.keys()
        != {"after_create_sequence", "operation", "protocol", "request_id", "version"}
        or value.get("operation") != "query"
    ):
        _fail()
    return ReconciliationRequest(
        _uuid(value["request_id"]),
        _sequence(value["after_create_sequence"], allow_zero=True),
    )


def _proof_mapping(tombstone: ReconciliationTombstone) -> dict[str, object]:
    proof = tombstone.proof
    return {
        "attempt_id": str(proof.attempt_id),
        "create_sequence": tombstone.create_sequence,
        "empty_evidence": proof.empty_evidence.value,
        "exit_evidence": proof.exit_evidence.value,
        "policy_revision": proof.policy_revision,
        "policy_specification": tombstone.policy_specification.value,
        "principal_id": str(proof.principal.principal_id),
        "proof_id": str(proof.proof_id),
        "removal_evidence": proof.removal_evidence.value,
        "unit_id": str(proof.unit_id),
    }


def encode_response(response: ReconciliationResult) -> bytes:
    if type(response) is ReconciliationErrorResponse:
        if (
            type(response.request_id) is not UUID
            or type(response.category) is not BrokerErrorCategory
        ):
            _fail()
        return _frame(
            {
                "category": response.category,
                "operation": "query",
                "outcome": "error",
                "protocol": PROTOCOL_NAME,
                "request_id": str(response.request_id),
                "version": PROTOCOL_VERSION,
            }
        )
    if type(response) is not ReconciliationResponse:
        _fail()
    tombstone = (
        None if response.tombstone is None else _proof_mapping(response.tombstone)
    )
    return _frame(
        {
            "after_create_sequence": response.after_create_sequence,
            "create_sequence_high_water": response.create_sequence_high_water,
            "done": response.done,
            "operation": "query",
            "outcome": "ok",
            "principal_id": str(response.principal_id),
            "protocol": PROTOCOL_NAME,
            "request_id": str(response.request_id),
            "tombstone": tombstone,
            "version": PROTOCOL_VERSION,
        }
    )


def decode_response(frame: bytes) -> ReconciliationResult:
    value = _decode(frame)
    if value.get("operation") != "query":
        _fail()
    if value.get("outcome") == "error":
        if value.keys() != {
            "category",
            "operation",
            "outcome",
            "protocol",
            "request_id",
            "version",
        } or not isinstance(value.get("category"), str):
            _fail()
        try:
            return ReconciliationErrorResponse(
                _uuid(value["request_id"]), BrokerErrorCategory(value["category"])
            )
        except ValueError:
            _fail()
    if (
        value.keys()
        != {
            "after_create_sequence",
            "create_sequence_high_water",
            "done",
            "operation",
            "outcome",
            "principal_id",
            "protocol",
            "request_id",
            "tombstone",
            "version",
        }
        or value.get("outcome") != "ok"
        or type(value.get("done")) is not bool
    ):
        _fail()
    principal_id = _uuid(value["principal_id"])
    raw = value["tombstone"]
    tombstone = None
    if raw is not None:
        if not isinstance(raw, dict) or raw.keys() != {
            "attempt_id",
            "create_sequence",
            "empty_evidence",
            "exit_evidence",
            "policy_revision",
            "policy_specification",
            "principal_id",
            "proof_id",
            "removal_evidence",
            "unit_id",
        }:
            _fail()
        proof_principal = _uuid(raw["principal_id"])
        if proof_principal != principal_id or not isinstance(
            raw["policy_revision"], str
        ):
            _fail()
        try:
            proof = TerminationProof(
                _uuid(raw["proof_id"]),
                _uuid(raw["attempt_id"]),
                _uuid(raw["unit_id"]),
                AuthenticatedPrincipal(proof_principal),
                raw["policy_revision"],
                EvidenceDigest(raw["exit_evidence"]),
                EvidenceDigest(raw["empty_evidence"]),
                EvidenceDigest(raw["removal_evidence"]),
            )
            tombstone = ReconciliationTombstone(
                _sequence(raw["create_sequence"], allow_zero=False),
                EvidenceDigest(raw["policy_specification"]),
                proof,
            )
        except TypeError, ValueError:
            _fail()
    try:
        done = value["done"]
        if type(done) is not bool:
            _fail()
        return ReconciliationResponse(
            _uuid(value["request_id"]),
            principal_id,
            _sequence(value["after_create_sequence"], allow_zero=True),
            _sequence(value["create_sequence_high_water"], allow_zero=True),
            tombstone,
            done,
        )
    except ValueError:
        _fail()
