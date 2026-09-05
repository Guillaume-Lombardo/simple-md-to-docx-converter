from __future__ import annotations

import hashlib
import json
from typing import Any, cast
from uuid import UUID

import pytest

from markweave.broker import workspace_protocol
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import RuntimeChannelLimits
from markweave.broker.protocol import ReadyRequest, encode_request
from markweave.broker.workspace_protocol import (
    WorkspaceCollectRequest,
    WorkspaceErrorResponse,
    WorkspaceFailureResponse,
    WorkspaceOperation,
    WorkspacePendingResponse,
    WorkspaceStageHeader,
    WorkspaceStageReceipt,
    WorkspaceStageRequest,
    WorkspaceSuccessResponse,
    bind_workspace_result,
    bind_workspace_source,
    collect_response,
    decode_workspace_request_header,
    decode_workspace_response_header,
    encode_workspace_request,
    encode_workspace_response,
    frame_protocol,
    receipt_for,
)
from markweave.reversions.errors import ReverseErrorCategory
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptSuccess,
    ReverseContentLimits,
    ReverseOutputMode,
)

pytestmark = pytest.mark.unit

REQUEST = UUID("10000000-0000-4000-8000-000000000001")
ATTEMPT = UUID("20000000-0000-4000-8000-000000000002")
UNIT = UUID("30000000-0000-4000-8000-000000000003")
INCARNATION = UUID("40000000-0000-4000-8000-000000000004")
CHANNEL = RuntimeChannelLimits(1000, 2000)
LIMITS = ReverseContentLimits(
    1000, 2000, 100, 10, 10, 100, 10, 5, 2, 100, 200, 500, 1000
)


def _stage(source: bytes = b"private") -> WorkspaceStageRequest:
    return WorkspaceStageRequest(REQUEST, 7, ATTEMPT, UNIT, 3, ".docx", LIMITS, source)


def _header(wire: bytes) -> tuple[bytes, bytes]:
    size = int.from_bytes(wire[:4], "big")
    return wire[: 4 + size], wire[4 + size :]


def _replace_header(wire: bytes, mutate) -> bytes:
    header, payload = _header(wire)
    value = json.loads(header[4:])
    mutate(value)
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")
    return len(encoded).to_bytes(4, "big") + encoded + payload


def test_stage_golden_is_canonical_header_raw_payload_and_digest_bound() -> None:
    request = _stage()
    wire = encode_workspace_request(request)
    header, payload = _header(wire)
    value = json.loads(header[4:])

    assert len(header) <= 4100
    assert payload == b"private"
    assert value == {
        "attempt_id": str(ATTEMPT),
        "create_sequence": 3,
        "extension": ".docx",
        "limits": {
            name: getattr(LIMITS, name)
            for name in ReverseContentLimits.__dataclass_fields__
        },
        "operation": "stage",
        "protocol": "markweave-reverse-broker-workspace",
        "request_id": str(REQUEST),
        "sequence": 7,
        "source_length": 7,
        "source_sha256": f"sha256:{hashlib.sha256(b'private').hexdigest()}",
        "unit_id": str(UNIT),
        "version": 1,
    }
    decoded = decode_workspace_request_header(header, CHANNEL)
    assert isinstance(decoded, WorkspaceStageHeader)
    assert bind_workspace_source(decoded, payload) == request
    assert "private" not in repr(request)


def test_lifecycle_v1_golden_remains_byte_for_byte() -> None:
    request = ReadyRequest(REQUEST, 7)
    payload = (
        b'{"operation":"ready","protocol":"markweave-reverse-broker",'
        b'"request_id":"10000000-0000-4000-8000-000000000001",'
        b'"sequence":7,"version":1}'
    )
    assert encode_request(request) == len(payload).to_bytes(4, "big") + payload
    assert (
        frame_protocol(encode_request(request)[: 4 + len(payload)])
        == "markweave-reverse-broker"
    )
    assert frame_protocol(b"bad") is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(version=True),
        lambda value: value.update(sequence=0),
        lambda value: value.update(extension=".exe"),
        lambda value: value.update(source_length=1001),
        lambda value: value.update(source_sha256="sha256:" + "A" * 64),
        lambda value: value["limits"].update(max_input_bytes=1001),
        lambda value: value.update(extra=True),
    ],
)
def test_stage_rejects_noncanonical_identity_type_extension_and_bounds(mutate) -> None:
    wire = _replace_header(encode_workspace_request(_stage()), mutate)
    header, _ = _header(wire)
    with pytest.raises(BrokerError) as caught:
        decode_workspace_request_header(header, CHANNEL)
    assert caught.value.category is BrokerErrorCategory.PROTOCOL_ERROR


def test_stage_rejects_wrong_length_digest_and_noncanonical_header() -> None:
    wire = encode_workspace_request(_stage())
    header, payload = _header(wire)
    decoded = decode_workspace_request_header(header, CHANNEL)
    assert isinstance(decoded, WorkspaceStageHeader)
    for invalid in (payload[:-1], payload + b"x", b"changed"):
        with pytest.raises(BrokerError):
            bind_workspace_source(decoded, invalid)
    with pytest.raises(BrokerError):
        decode_workspace_request_header(header + b" ", CHANNEL)
    with pytest.raises(BrokerError):
        decode_workspace_request_header(b"\x00\x00\x10\x01", CHANNEL)
    with pytest.raises(BrokerError):
        decode_workspace_request_header(header, cast(Any, "bad-limits"))
    with pytest.raises(BrokerError):
        bind_workspace_source(cast(Any, object()), payload)


def test_low_level_header_rejects_invalid_json_values_and_oversize() -> None:
    with pytest.raises(BrokerError):
        workspace_protocol._canonical({"invalid": object()})
    with pytest.raises(BrokerError):
        workspace_protocol._canonical({"oversize": "x" * 4096})
    wire = encode_workspace_request(_stage())
    for mutate in (
        lambda value: value.update(request_id="bad"),
        lambda value: value.update(request_id="AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        lambda value: value.update(source_length=True),
        lambda value: value.update(operation="unknown"),
        lambda value: value.update(
            source_length=999,
            limits={**value["limits"], "max_input_bytes": 998},
        ),
    ):
        changed = _replace_header(wire, mutate)
        with pytest.raises(BrokerError):
            decode_workspace_request_header(_header(changed)[0], CHANNEL)


def test_collect_and_every_response_shape_round_trip() -> None:
    receipt = WorkspaceStageReceipt(REQUEST, 7, ATTEMPT, UNIT, 3, INCARNATION)
    collect = WorkspaceCollectRequest(
        UUID("50000000-0000-4000-8000-000000000005"),
        8,
        REQUEST,
        7,
        ATTEMPT,
        UNIT,
        3,
        INCARNATION,
    )
    header, payload = _header(encode_workspace_request(collect))
    assert not payload
    assert decode_workspace_request_header(header, CHANNEL) == collect

    responses = (
        receipt,
        WorkspacePendingResponse(collect.request_id, receipt),
        WorkspaceFailureResponse(
            collect.request_id, receipt, ReverseErrorCategory.MALFORMED
        ),
        WorkspaceSuccessResponse(
            collect.request_id, receipt, ReverseOutputMode.MARKDOWN, b"result"
        ),
        WorkspaceErrorResponse(
            collect.request_id,
            WorkspaceOperation.COLLECT,
            BrokerErrorCategory.REPLAY_REJECTED,
        ),
    )
    for response in responses:
        header, payload = _header(encode_workspace_response(response, CHANNEL))
        decoded, length, digest = decode_workspace_response_header(header, CHANNEL)
        assert length == len(payload)
        assert bind_workspace_result(decoded, payload, digest) == response
    assert "result" not in repr(responses[3])


def test_receipt_derives_incarnation_separately_from_stage_wire() -> None:
    receipt = receipt_for(_stage(), INCARNATION)
    assert receipt.incarnation_id == INCARNATION
    with pytest.raises(BrokerError):
        receipt_for(_stage(), cast(Any, "not-a-uuid"))


@pytest.mark.parametrize(
    "candidate",
    [
        cast(Any, object()),
        WorkspaceStageRequest(
            cast(Any, "bad"), 7, ATTEMPT, UNIT, 3, ".docx", LIMITS, b"x"
        ),
        WorkspaceStageRequest(REQUEST, 0, ATTEMPT, UNIT, 3, ".docx", LIMITS, b"x"),
        WorkspaceStageRequest(REQUEST, 7, ATTEMPT, UNIT, 3, ".exe", LIMITS, b"x"),
        WorkspaceCollectRequest(
            REQUEST, 7, REQUEST, 7, ATTEMPT, UNIT, 3, cast(Any, "bad")
        ),
    ],
)
def test_request_encoder_rejects_invalid_models(candidate: Any) -> None:
    with pytest.raises(BrokerError):
        encode_workspace_request(candidate)


def test_decoder_rejects_duplicate_nonobject_wrong_protocol_and_invalid_limits() -> (
    None
):
    invalid_payloads = (
        b'{"protocol":"markweave-reverse-broker-workspace","protocol":"x","version":1}',
        b"[]",
        b'{"operation":"stage","protocol":"wrong","version":1}',
        b'{"operation":"stage","protocol":"markweave-reverse-broker-workspace","version":NaN}',
    )
    for payload in invalid_payloads:
        with pytest.raises(BrokerError):
            decode_workspace_request_header(
                len(payload).to_bytes(4, "big") + payload, CHANNEL
            )

    wire = encode_workspace_request(_stage())
    for replacement in (
        None,
        {},
        {**json.loads(_header(wire)[0][4:])["limits"], "max_output_bytes": 1},
    ):
        changed = _replace_header(
            wire,
            lambda value, replacement=replacement: value.update(limits=replacement),
        )
        with pytest.raises(BrokerError):
            decode_workspace_request_header(_header(changed)[0], CHANNEL)


def test_response_decoder_rejects_wrong_operation_receipt_and_categories() -> None:
    receipt = WorkspaceStageReceipt(REQUEST, 7, ATTEMPT, UNIT, 3, INCARNATION)
    frames = (
        encode_workspace_response(receipt, CHANNEL),
        encode_workspace_response(
            WorkspaceFailureResponse(REQUEST, receipt, ReverseErrorCategory.MALFORMED),
            CHANNEL,
        ),
        encode_workspace_response(
            WorkspaceSuccessResponse(
                REQUEST, receipt, ReverseOutputMode.MARKDOWN, b"result"
            ),
            CHANNEL,
        ),
        encode_workspace_response(
            WorkspaceErrorResponse(
                REQUEST, WorkspaceOperation.COLLECT, BrokerErrorCategory.RUNTIME_FAILURE
            ),
            CHANNEL,
        ),
    )
    mutations = (
        lambda value: value.update(receipt_request_id=str(UUID(int=9))),
        lambda value: value.update(category="unknown"),
        lambda value: value.update(mode="unknown"),
        lambda value: value.update(operation="unknown"),
        lambda value: value.update(outcome="unknown"),
    )
    for frame, mutate in zip((*frames, frames[2]), mutations, strict=True):
        changed = _replace_header(frame, mutate)
        with pytest.raises(BrokerError):
            decode_workspace_response_header(_header(changed)[0], CHANNEL)


def test_response_encoder_rejects_invalid_models_and_nonpayload_binding() -> None:
    receipt = WorkspaceStageReceipt(REQUEST, 7, ATTEMPT, UNIT, 3, INCARNATION)
    invalid = (
        cast(Any, object()),
        WorkspaceErrorResponse(
            REQUEST, cast(Any, "bad"), BrokerErrorCategory.RUNTIME_FAILURE
        ),
        WorkspaceFailureResponse(REQUEST, receipt, cast(Any, "bad")),
        WorkspaceFailureResponse(REQUEST, receipt, ReverseErrorCategory.CANCELLED),
        WorkspaceSuccessResponse(REQUEST, receipt, cast(Any, "bad"), b"result"),
        WorkspaceSuccessResponse(REQUEST, receipt, ReverseOutputMode.MARKDOWN, b""),
    )
    for response in invalid:
        with pytest.raises(BrokerError):
            encode_workspace_response(response, CHANNEL)
    with pytest.raises(BrokerError):
        encode_workspace_response(
            WorkspaceSuccessResponse(
                REQUEST, receipt, ReverseOutputMode.MARKDOWN, b"x" * 2001
            ),
            CHANNEL,
        )
    with pytest.raises(BrokerError):
        encode_workspace_response(receipt, cast(Any, object()))
    failure = encode_workspace_response(
        WorkspaceFailureResponse(REQUEST, receipt, ReverseErrorCategory.MALFORMED),
        CHANNEL,
    )
    invalid_child_category = _replace_header(
        failure, lambda value: value.update(category="cancelled")
    )
    with pytest.raises(BrokerError):
        decode_workspace_response_header(_header(invalid_child_category)[0], CHANNEL)
    with pytest.raises(BrokerError):
        bind_workspace_result(
            WorkspacePendingResponse(REQUEST, receipt), b"extra", None
        )


def test_response_rejects_truncated_extra_wrong_digest_and_output_bound() -> None:
    receipt = WorkspaceStageReceipt(REQUEST, 7, ATTEMPT, UNIT, 3, INCARNATION)
    response = WorkspaceSuccessResponse(
        REQUEST, receipt, ReverseOutputMode.MARKDOWN, b"result"
    )
    wire = encode_workspace_response(response, CHANNEL)
    header, payload = _header(wire)
    decoded, _, digest = decode_workspace_response_header(header, CHANNEL)
    with pytest.raises(BrokerError):
        bind_workspace_result(decoded, payload[:-1], digest)
    for mutate in (
        lambda value: value.update(result_length=2001),
        lambda value: value.update(result_sha256="sha256:" + "b" * 64),
        lambda value: value.update(extra=True),
    ):
        changed, _ = _header(_replace_header(wire, mutate))
        with pytest.raises(BrokerError):
            decoded, length, changed_digest = decode_workspace_response_header(
                changed, CHANNEL
            )
            bind_workspace_result(decoded, payload[:length], changed_digest)


def test_runtime_response_mapping_covers_pending_failure_success_and_invalid() -> None:
    receipt = WorkspaceStageReceipt(REQUEST, 7, ATTEMPT, UNIT, 3, INCARNATION)
    assert isinstance(
        collect_response(REQUEST, receipt, None), WorkspacePendingResponse
    )
    assert isinstance(
        collect_response(
            REQUEST,
            receipt,
            ReverseAttemptFailure(ATTEMPT, ReverseErrorCategory.MALFORMED),
        ),
        WorkspaceFailureResponse,
    )
    assert isinstance(
        collect_response(
            REQUEST,
            receipt,
            ReverseAttemptSuccess(ATTEMPT, ReverseOutputMode.MARKDOWN, b"result"),
        ),
        WorkspaceSuccessResponse,
    )
    with pytest.raises(BrokerError):
        collect_response(REQUEST, receipt, cast(Any, object()))
