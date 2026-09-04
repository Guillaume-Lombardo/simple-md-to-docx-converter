from __future__ import annotations

import json
from typing import Any, cast
from uuid import UUID

import pytest

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import (
    MAX_SEQUENCE,
    AuthenticatedPrincipal,
    EvidenceDigest,
    ManagedUnitState,
    TerminationProof,
)
from markweave.broker.protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_NAME,
    PROTOCOL_VERSION,
    AcknowledgeRequest,
    AcknowledgeResponse,
    BrokerOperation,
    BrokerRequest,
    BrokerResponse,
    CreateRequest,
    CreateResponse,
    ErrorResponse,
    ProofRequest,
    ProofResponse,
    ReadyRequest,
    ReadyResponse,
    StatusRequest,
    StatusResponse,
    TerminateRequest,
    TerminateResponse,
    decode_length_prefix,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)

pytestmark = pytest.mark.unit

REQUEST_ID = UUID("00000000-0000-4000-8000-000000000001")
ATTEMPT_ID = UUID("00000000-0000-4000-8000-000000000002")
UNIT_ID = UUID("00000000-0000-4000-8000-000000000003")
PROOF_ID = UUID("00000000-0000-4000-8000-000000000004")
PRINCIPAL_ID = UUID("00000000-0000-4000-8000-000000000005")
DIGEST = "sha256:" + "a" * 64


def _frame(value: object, *, canonical: bool = True) -> bytes:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=canonical,
        separators=(",", ":") if canonical else None,
    ).encode("ascii")
    return len(payload).to_bytes(4, "big") + payload


def _mapping(operation: str = "ready", **extra: object) -> dict[str, object]:
    return {
        "operation": operation,
        "protocol": PROTOCOL_NAME,
        "request_id": str(REQUEST_ID),
        "sequence": 1,
        "version": PROTOCOL_VERSION,
        **extra,
    }


@pytest.mark.parametrize(
    "broker_request",
    [
        CreateRequest(REQUEST_ID, 1, ATTEMPT_ID),
        StatusRequest(REQUEST_ID, 2, ATTEMPT_ID, UNIT_ID),
        TerminateRequest(REQUEST_ID, 3, ATTEMPT_ID, UNIT_ID),
        ProofRequest(REQUEST_ID, 4, ATTEMPT_ID, UNIT_ID),
        AcknowledgeRequest(REQUEST_ID, 5, ATTEMPT_ID, UNIT_ID, PROOF_ID),
        ReadyRequest(REQUEST_ID, MAX_SEQUENCE),
    ],
)
def test_every_approved_operation_has_a_canonical_round_trip(
    broker_request: BrokerRequest,
) -> None:
    encoded = encode_request(broker_request)

    assert decode_length_prefix(encoded[:4]) == len(encoded) - 4
    assert decode_request(encoded) == broker_request
    assert encode_request(decode_request(encoded)) == encoded


def test_create_frame_cannot_express_runtime_policy() -> None:
    frame = encode_request(CreateRequest(REQUEST_ID, 1, ATTEMPT_ID))
    metadata = json.loads(frame[4:])

    assert set(metadata) == {
        "attempt_id",
        "operation",
        "protocol",
        "request_id",
        "sequence",
        "version",
    }
    forbidden = {
        "image",
        "argv",
        "mount",
        "network",
        "credential",
        "runtime",
        "policy",
        "limits",
        "labels",
    }
    assert forbidden.isdisjoint(metadata)


@pytest.mark.parametrize("prefix", [b"", b"\0", b"\0\0\0", b"\0\0\0\0"])
def test_length_prefix_rejects_wrong_or_empty_size(prefix: bytes) -> None:
    with pytest.raises(BrokerError) as captured:
        decode_length_prefix(prefix)
    assert captured.value.category is BrokerErrorCategory.PROTOCOL_ERROR


def test_length_prefix_rejects_oversize_before_payload_read() -> None:
    prefix = (MAX_FRAME_BYTES + 1).to_bytes(4, "big")
    with pytest.raises(BrokerError, match="protocol request"):
        decode_length_prefix(prefix)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda frame: frame[:3],
        lambda frame: frame[:-1],
        lambda frame: frame + b"x",
        lambda frame: (len(frame)).to_bytes(4, "big") + frame[4:],
    ],
)
def test_decoder_rejects_truncated_trailing_or_mismatched_frames(mutate) -> None:
    frame = encode_request(ReadyRequest(REQUEST_ID, 1))
    with pytest.raises(BrokerError, match="protocol request"):
        decode_request(mutate(frame))


def test_decoder_rejects_duplicate_keys() -> None:
    payload = (
        b'{"operation":"ready","operation":"ready","protocol":"'
        + PROTOCOL_NAME.encode()
        + b'","request_id":"00000000-0000-4000-8000-000000000001",'
        b'"sequence":1,"version":1}'
    )
    with pytest.raises(BrokerError, match="protocol request"):
        decode_request(len(payload).to_bytes(4, "big") + payload)


def test_decoder_rejects_noncanonical_json() -> None:
    with pytest.raises(BrokerError, match="protocol request"):
        decode_request(_frame(_mapping(), canonical=False))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("protocol", "other"),
        ("version", 2),
        ("version", True),
        ("sequence", 0),
        ("sequence", MAX_SEQUENCE + 1),
        ("sequence", True),
        ("sequence", 1.0),
        ("request_id", "00000000-0000-4000-8000-00000000000A"),
    ],
)
def test_decoder_rejects_invalid_header_fields(field: str, value: object) -> None:
    message = _mapping()
    message[field] = value
    with pytest.raises(BrokerError, match="protocol request"):
        decode_request(_frame(message))


@pytest.mark.parametrize("operation", ["run", "delete", "", 1, True])
def test_decoder_rejects_unknown_or_non_string_operations(operation: object) -> None:
    message = _mapping()
    message["operation"] = operation
    with pytest.raises(BrokerError, match="protocol request"):
        decode_request(_frame(message))


@pytest.mark.parametrize(
    "extra",
    ["image", "argv", "mount", "network", "credential", "runtime", "limits"],
)
def test_decoder_rejects_every_runtime_override(extra: str) -> None:
    message = _mapping("create", attempt_id=str(ATTEMPT_ID))
    message[extra] = "caller-controlled"
    with pytest.raises(BrokerError, match="protocol request"):
        decode_request(_frame(message))


@pytest.mark.parametrize(
    "message",
    [
        _mapping("create"),
        _mapping("status", attempt_id=str(ATTEMPT_ID)),
        _mapping("terminate", attempt_id=str(ATTEMPT_ID), unit_id=str(UNIT_ID), x=1),
        _mapping(
            "proof",
            attempt_id=str(ATTEMPT_ID),
            unit_id=str(UNIT_ID),
            proof_id=str(PROOF_ID),
        ),
        _mapping("ack", attempt_id=str(ATTEMPT_ID), unit_id=str(UNIT_ID)),
        _mapping("ready", attempt_id=str(ATTEMPT_ID)),
    ],
)
def test_decoder_requires_each_operation_exact_schema(
    message: dict[str, object],
) -> None:
    with pytest.raises(BrokerError, match="protocol request"):
        decode_request(_frame(message))


@pytest.mark.parametrize("invalid", [None, "id", 1, True])
def test_encoder_rejects_invalid_request_identity(invalid: object) -> None:
    with pytest.raises(BrokerError, match="protocol request"):
        encode_request(ReadyRequest(cast(Any, invalid), 1))


def test_encoder_rejects_invalid_attempt_and_unknown_request_models() -> None:
    with pytest.raises(BrokerError, match="protocol request"):
        encode_request(CreateRequest(REQUEST_ID, 1, cast(Any, "attempt")))
    with pytest.raises(BrokerError, match="protocol request"):
        encode_request(cast(Any, object()))


@pytest.mark.parametrize("sequence", [0, -1, MAX_SEQUENCE + 1, True, 1.0])
def test_encoder_rejects_invalid_sequences(sequence: object) -> None:
    with pytest.raises(BrokerError, match="protocol request"):
        encode_request(ReadyRequest(REQUEST_ID, cast(Any, sequence)))


def test_decoder_rejects_non_ascii_and_non_object_payloads() -> None:
    for payload in (b'"ready"', b"[]", b"{\xff}"):
        with pytest.raises(BrokerError, match="protocol request"):
            decode_request(len(payload).to_bytes(4, "big") + payload)


@pytest.mark.parametrize("value", [None, 1, True, "not-a-uuid"])
def test_request_decoder_rejects_noncanonical_uuid_values(value: object) -> None:
    with pytest.raises(BrokerError, match="protocol request"):
        decode_request(_frame(_mapping("create", attempt_id=value)))


def _proof() -> TerminationProof:
    evidence = EvidenceDigest(DIGEST)
    return TerminationProof(
        PROOF_ID,
        ATTEMPT_ID,
        UNIT_ID,
        AuthenticatedPrincipal(PRINCIPAL_ID),
        "t71-v1",
        evidence,
        evidence,
        evidence,
    )


@pytest.mark.parametrize(
    "broker_response",
    [
        CreateResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, ManagedUnitState.CREATE_INTENT),
        StatusResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, ManagedUnitState.CREATED),
        TerminateResponse(REQUEST_ID, _proof()),
        ProofResponse(REQUEST_ID, _proof()),
        AcknowledgeResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID, True),
        ReadyResponse(REQUEST_ID, True),
        ErrorResponse(
            REQUEST_ID,
            BrokerOperation.CREATE,
            BrokerErrorCategory.RECONCILIATION_INCOMPLETE,
        ),
    ],
)
def test_every_operation_response_has_a_canonical_round_trip(
    broker_response: BrokerResponse,
) -> None:
    encoded = encode_response(broker_response)
    assert decode_response(encoded) == broker_response
    assert encode_response(decode_response(encoded)) == encoded


def test_proof_response_contains_only_content_free_stable_evidence() -> None:
    encoded = encode_response(ProofResponse(REQUEST_ID, _proof()))
    metadata = json.loads(encoded[4:])

    assert metadata == {
        "attempt_id": str(ATTEMPT_ID),
        "empty_evidence": DIGEST,
        "exit_evidence": DIGEST,
        "operation": "proof",
        "outcome": "ok",
        "policy_revision": "t71-v1",
        "principal_id": str(PRINCIPAL_ID),
        "proof_id": str(PROOF_ID),
        "protocol": PROTOCOL_NAME,
        "removal_evidence": DIGEST,
        "request_id": str(REQUEST_ID),
        "unit_id": str(UNIT_ID),
        "version": PROTOCOL_VERSION,
    }


def test_terminate_response_returns_the_exact_content_free_proof() -> None:
    proof = _proof()
    encoded = encode_response(TerminateResponse(REQUEST_ID, proof))
    metadata = json.loads(encoded[4:])

    assert metadata == {
        "attempt_id": str(ATTEMPT_ID),
        "empty_evidence": DIGEST,
        "exit_evidence": DIGEST,
        "operation": "terminate",
        "outcome": "ok",
        "policy_revision": "t71-v1",
        "principal_id": str(PRINCIPAL_ID),
        "proof_id": str(PROOF_ID),
        "protocol": PROTOCOL_NAME,
        "removal_evidence": DIGEST,
        "request_id": str(REQUEST_ID),
        "unit_id": str(UNIT_ID),
        "version": PROTOCOL_VERSION,
    }
    decoded = decode_response(encoded)
    assert decoded == TerminateResponse(REQUEST_ID, proof)


def test_ack_response_binds_ids_and_explicit_success() -> None:
    encoded = encode_response(
        AcknowledgeResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID, True)
    )

    assert json.loads(encoded[4:]) == {
        "acknowledged": True,
        "attempt_id": str(ATTEMPT_ID),
        "operation": "ack",
        "outcome": "ok",
        "proof_id": str(PROOF_ID),
        "protocol": PROTOCOL_NAME,
        "request_id": str(REQUEST_ID),
        "unit_id": str(UNIT_ID),
        "version": PROTOCOL_VERSION,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda frame: frame[:-1],
        lambda frame: frame + b"x",
        lambda frame: frame[:4] + frame[4:].replace(b'"ready":true', b'"ready":1'),
        lambda frame: frame[:4] + frame[4:].replace(b'"version":1', b'"version":true'),
    ],
)
def test_response_decoder_rejects_truncation_trailing_and_bool_lookalikes(
    mutate,
) -> None:
    frame = encode_response(ReadyResponse(REQUEST_ID, True))
    with pytest.raises(BrokerError, match="protocol request"):
        decode_response(mutate(frame))


def test_response_decoder_rejects_duplicate_extra_and_noncanonical_fields() -> None:
    mapping = {
        "operation": "ready",
        "outcome": "ok",
        "protocol": PROTOCOL_NAME,
        "ready": True,
        "request_id": str(REQUEST_ID),
        "version": PROTOCOL_VERSION,
    }
    invalid_frames = [
        _frame({**mapping, "backend": "podman"}),
        _frame(mapping, canonical=False),
    ]
    canonical = json.dumps(mapping, sort_keys=True, separators=(",", ":"))
    duplicate = canonical.replace('"ready":true', '"ready":true,"ready":true')
    invalid_frames.append(len(duplicate).to_bytes(4, "big") + duplicate.encode())
    for frame in invalid_frames:
        with pytest.raises(BrokerError, match="protocol request"):
            decode_response(frame)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation", "unknown"),
        ("operation", True),
        ("outcome", "pending"),
        ("outcome", True),
        ("protocol", "other"),
        ("version", True),
        ("request_id", "not-a-uuid"),
    ],
)
def test_response_decoder_rejects_invalid_common_fields(
    field: str, value: object
) -> None:
    mapping = {
        "operation": "ready",
        "outcome": "ok",
        "protocol": PROTOCOL_NAME,
        "ready": True,
        "request_id": str(REQUEST_ID),
        "version": PROTOCOL_VERSION,
    }
    mapping[field] = value
    with pytest.raises(BrokerError, match="protocol request"):
        decode_response(_frame(mapping))


def test_error_response_rejects_unknown_category_and_extra_details() -> None:
    mapping = {
        "category": "not_a_category",
        "operation": "create",
        "outcome": "error",
        "protocol": PROTOCOL_NAME,
        "request_id": str(REQUEST_ID),
        "version": PROTOCOL_VERSION,
    }
    with pytest.raises(BrokerError, match="protocol request"):
        decode_response(_frame(mapping))
    mapping["category"] = BrokerErrorCategory.RUNTIME_FAILURE
    mapping["detail"] = "/secret/runtime/path"
    with pytest.raises(BrokerError, match="protocol request"):
        decode_response(_frame(mapping))


def test_error_response_rejects_non_string_category() -> None:
    mapping = {
        "category": True,
        "operation": "create",
        "outcome": "error",
        "protocol": PROTOCOL_NAME,
        "request_id": str(REQUEST_ID),
        "version": PROTOCOL_VERSION,
    }
    with pytest.raises(BrokerError, match="protocol request"):
        decode_response(_frame(mapping))


def test_inventory_failure_has_fixed_content_free_wire_error() -> None:
    error = BrokerError(BrokerErrorCategory.INVENTORY_FAILURE)
    response = ErrorResponse(
        REQUEST_ID,
        BrokerOperation.CREATE,
        BrokerErrorCategory.INVENTORY_FAILURE,
    )

    assert str(error) == "The broker inventory operation failed."
    assert decode_response(encode_response(response)) == response


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(state="unknown"),
        lambda value: value.update(state=True),
        lambda value: value.pop("unit_id"),
    ],
)
def test_unit_response_requires_exact_valid_state_schema(mutate) -> None:
    encoded = encode_response(
        StatusResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, ManagedUnitState.CREATED)
    )
    mapping = json.loads(encoded[4:])
    mutate(mapping)
    with pytest.raises(BrokerError, match="protocol request"):
        decode_response(_frame(mapping))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("policy_revision", "UPPER"),
        ("exit_evidence", "sha256:bad"),
        ("empty_evidence", True),
        ("principal_id", "not-a-uuid"),
    ],
)
@pytest.mark.parametrize("response_type", [ProofResponse, TerminateResponse])
def test_proof_response_rejects_invalid_proof_fields(
    field: str, value: object, response_type: type[ProofResponse | TerminateResponse]
) -> None:
    encoded = encode_response(response_type(REQUEST_ID, _proof()))
    mapping = json.loads(encoded[4:])
    mapping[field] = value
    with pytest.raises(BrokerError, match="protocol request"):
        decode_response(_frame(mapping))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.pop("acknowledged"),
        lambda value: value.update(acknowledged=False),
        lambda value: value.update(acknowledged=1),
        lambda value: value.update(idempotent=True),
        lambda value: value.update(proof_id=True),
        lambda value: value.pop("unit_id"),
    ],
)
def test_ack_response_requires_exact_bound_success_schema(mutate) -> None:
    encoded = encode_response(
        AcknowledgeResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, PROOF_ID, True)
    )
    mapping = json.loads(encoded[4:])
    mutate(mapping)
    with pytest.raises(BrokerError, match="protocol request"):
        decode_response(_frame(mapping))


def test_response_encoder_rejects_invalid_error_proof_and_unknown_models() -> None:
    with pytest.raises(BrokerError, match="protocol request"):
        encode_response(
            ErrorResponse(
                REQUEST_ID,
                cast(Any, "create"),
                BrokerErrorCategory.RUNTIME_FAILURE,
            )
        )
    with pytest.raises(BrokerError, match="protocol request"):
        encode_response(ProofResponse(REQUEST_ID, cast(Any, object())))
    with pytest.raises(BrokerError, match="protocol request"):
        encode_response(TerminateResponse(REQUEST_ID, cast(Any, object())))
    with pytest.raises(BrokerError, match="protocol request"):
        encode_response(cast(Any, object()))


def test_response_encoder_rejects_bool_lookalike_and_invalid_state() -> None:
    with pytest.raises(BrokerError, match="protocol request"):
        encode_response(ReadyResponse(REQUEST_ID, cast(Any, 1)))
    with pytest.raises(BrokerError, match="protocol request"):
        encode_response(
            StatusResponse(REQUEST_ID, ATTEMPT_ID, UNIT_ID, cast(Any, "created"))
        )
    with pytest.raises(BrokerError, match="protocol request"):
        encode_response(
            AcknowledgeResponse(
                REQUEST_ID,
                ATTEMPT_ID,
                UNIT_ID,
                PROOF_ID,
                cast(Any, False),
            )
        )
