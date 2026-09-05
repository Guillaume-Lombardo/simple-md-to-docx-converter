"""Canonical bounded workspace subprotocol for reverse-attempt payloads."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Never, cast
from uuid import UUID

from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.models import MAX_SEQUENCE, RuntimeChannelLimits
from markweave.reversions.attempt_channel import CHILD_FAILURE_CATEGORIES
from markweave.reversions.errors import ReverseConversionError, ReverseErrorCategory
from markweave.reversions.formats import normalize_extension_hint
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptRequest,
    ReverseAttemptResponse,
    ReverseAttemptSuccess,
    ReverseContentLimits,
    ReverseOutputMode,
)

WORKSPACE_PROTOCOL_NAME = "markweave-reverse-broker-workspace"
WORKSPACE_PROTOCOL_VERSION = 1
WORKSPACE_HEADER_BYTES = 4096
WORKSPACE_LENGTH_PREFIX_BYTES = 4
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_LIMIT_FIELDS = tuple(ReverseContentLimits.__dataclass_fields__)


class WorkspaceOperation(StrEnum):
    """The closed workspace operation set."""

    STAGE = "stage"
    COLLECT = "collect"


@dataclass(frozen=True, slots=True)
class WorkspaceStageRequest:
    """One attempt-bound source transfer; source bytes are never represented."""

    request_id: UUID
    sequence: int
    attempt_id: UUID
    unit_id: UUID
    create_sequence: int
    extension: str
    limits: ReverseContentLimits
    source: bytes = field(repr=False)

    def reverse_request(self) -> ReverseAttemptRequest:
        """Build the existing runtime-port request after wire validation."""

        return ReverseAttemptRequest(
            self.attempt_id, self.extension, self.limits, self.source
        )


@dataclass(frozen=True, slots=True)
class WorkspaceStageHeader:
    """Validated bounded STAGE metadata, before its source allocation."""

    request_id: UUID
    sequence: int
    attempt_id: UUID
    unit_id: UUID
    create_sequence: int
    extension: str
    limits: ReverseContentLimits
    source_length: int
    source_sha256: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class WorkspaceStageReceipt:
    """Content-free receipt binding a successful runtime copy."""

    request_id: UUID
    stage_sequence: int
    attempt_id: UUID
    unit_id: UUID
    create_sequence: int
    incarnation_id: UUID


@dataclass(frozen=True, slots=True)
class WorkspaceCollectRequest:
    """Read-only collection bound to one exact STAGE receipt."""

    request_id: UUID
    sequence: int
    receipt_request_id: UUID
    stage_sequence: int
    attempt_id: UUID
    unit_id: UUID
    create_sequence: int
    incarnation_id: UUID


@dataclass(frozen=True, slots=True)
class WorkspacePendingResponse:
    request_id: UUID
    receipt: WorkspaceStageReceipt


@dataclass(frozen=True, slots=True)
class WorkspaceFailureResponse:
    request_id: UUID
    receipt: WorkspaceStageReceipt
    category: ReverseErrorCategory


@dataclass(frozen=True, slots=True)
class WorkspaceSuccessResponse:
    request_id: UUID
    receipt: WorkspaceStageReceipt
    mode: ReverseOutputMode
    result: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class WorkspaceErrorResponse:
    request_id: UUID
    operation: WorkspaceOperation
    category: BrokerErrorCategory


WorkspaceRequestHeader = WorkspaceStageHeader | WorkspaceCollectRequest
WorkspaceResponse = (
    WorkspaceStageReceipt
    | WorkspacePendingResponse
    | WorkspaceFailureResponse
    | WorkspaceSuccessResponse
    | WorkspaceErrorResponse
)


def _fail() -> Never:
    raise BrokerError(BrokerErrorCategory.PROTOCOL_ERROR)


def _canonical(value: dict[str, object]) -> bytes:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except TypeError, ValueError, UnicodeEncodeError, RecursionError:
        _fail()
    if not encoded or len(encoded) > WORKSPACE_HEADER_BYTES:
        _fail()
    return encoded


def _frame(value: dict[str, object]) -> bytes:
    encoded = _canonical(value)
    return len(encoded).to_bytes(WORKSPACE_LENGTH_PREFIX_BYTES, "big") + encoded


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _mapping(frame: bytes, *, require_workspace: bool = True) -> dict[str, object]:
    if type(frame) is not bytes or len(frame) < WORKSPACE_LENGTH_PREFIX_BYTES:
        _fail()
    size = int.from_bytes(frame[:WORKSPACE_LENGTH_PREFIX_BYTES], "big")
    if not 0 < size <= WORKSPACE_HEADER_BYTES or len(frame) != 4 + size:
        _fail()
    payload = frame[4:]
    try:
        value = json.loads(
            payload.decode("ascii"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except UnicodeDecodeError, ValueError, RecursionError:
        _fail()
    if type(value) is not dict or _canonical(value) != payload:
        _fail()
    if require_workspace and (
        value.get("protocol") != WORKSPACE_PROTOCOL_NAME
        or type(value.get("version")) is not int
        or value.get("version") != WORKSPACE_PROTOCOL_VERSION
    ):
        _fail()
    return value


def frame_protocol(frame: bytes) -> str | None:
    """Return only a canonical header's protocol discriminator."""

    try:
        value = _mapping(frame, require_workspace=False)
    except BrokerError:
        return None
    protocol = value.get("protocol")
    return protocol if isinstance(protocol, str) else None


def _uuid(value: object) -> UUID:
    if type(value) is not str:
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


def _positive(value: object) -> int:
    if type(value) is not int or value <= 0:
        _fail()
    return value


def _limits(value: object, channel: RuntimeChannelLimits) -> ReverseContentLimits:
    if type(value) is not dict:
        _fail()
    mapping = cast(dict[str, object], value)
    if set(mapping) != set(_LIMIT_FIELDS):
        _fail()
    try:
        limits = ReverseContentLimits(
            *(_positive(mapping[name]) for name in _LIMIT_FIELDS)
        )
    except ValueError:
        _fail()
    if (
        limits.max_input_bytes > channel.max_input_bytes
        or limits.max_output_bytes > channel.max_output_bytes
    ):
        _fail()
    return limits


def _identity(value: dict[str, object]) -> tuple[UUID, int, UUID, UUID, int]:
    return (
        _uuid(value.get("request_id")),
        _sequence(value.get("sequence")),
        _uuid(value.get("attempt_id")),
        _uuid(value.get("unit_id")),
        _sequence(value.get("create_sequence")),
    )


def _base(request_id: UUID, operation: WorkspaceOperation) -> dict[str, object]:
    if type(request_id) is not UUID:
        _fail()
    return {
        "operation": operation,
        "protocol": WORKSPACE_PROTOCOL_NAME,
        "request_id": str(request_id),
        "version": WORKSPACE_PROTOCOL_VERSION,
    }


def _request_identity(request: WorkspaceStageRequest) -> dict[str, object]:
    if any(
        type(value) is not UUID
        for value in (
            request.request_id,
            request.attempt_id,
            request.unit_id,
        )
    ):
        _fail()
    return {
        "attempt_id": str(request.attempt_id),
        "create_sequence": _sequence(request.create_sequence),
        "sequence": _sequence(request.sequence),
        "unit_id": str(request.unit_id),
    }


def encode_workspace_request(
    request: WorkspaceStageRequest | WorkspaceCollectRequest,
) -> bytes:
    """Encode one canonical request header and its exact optional raw payload."""

    if type(request) is WorkspaceStageRequest:
        if (
            type(request.limits) is not ReverseContentLimits
            or type(request.source) is not bytes
        ):
            _fail()
        try:
            extension = normalize_extension_hint(request.extension)
            _ = request.reverse_request()
        except ValueError, BrokerError, ReverseConversionError:
            _fail()
        value = _base(request.request_id, WorkspaceOperation.STAGE)
        value.update(_request_identity(request))
        value.update(
            extension=extension,
            limits={name: getattr(request.limits, name) for name in _LIMIT_FIELDS},
            source_length=len(request.source),
            source_sha256=f"sha256:{hashlib.sha256(request.source).hexdigest()}",
        )
        return _frame(value) + request.source
    if type(request) is WorkspaceCollectRequest:
        if any(
            type(value) is not UUID
            for value in (
                request.request_id,
                request.receipt_request_id,
                request.attempt_id,
                request.unit_id,
                request.incarnation_id,
            )
        ):
            _fail()
        value = _base(request.request_id, WorkspaceOperation.COLLECT)
        value.update(
            sequence=_sequence(request.sequence),
            receipt_request_id=str(request.receipt_request_id),
            stage_sequence=_sequence(request.stage_sequence),
            attempt_id=str(request.attempt_id),
            unit_id=str(request.unit_id),
            create_sequence=_sequence(request.create_sequence),
            incarnation_id=str(request.incarnation_id),
        )
        return _frame(value)
    _fail()


def decode_workspace_request_header(
    frame: bytes, channel: RuntimeChannelLimits
) -> WorkspaceRequestHeader:
    """Decode metadata and enforce channel bounds before payload allocation."""

    if type(channel) is not RuntimeChannelLimits:
        _fail()
    value = _mapping(frame)
    operation = value.get("operation")
    if operation == WorkspaceOperation.STAGE:
        expected = {
            "attempt_id",
            "create_sequence",
            "extension",
            "limits",
            "operation",
            "protocol",
            "request_id",
            "sequence",
            "source_length",
            "source_sha256",
            "unit_id",
            "version",
        }
        if set(value) != expected:
            _fail()
        request_id, sequence, attempt, unit, create_sequence = _identity(value)
        source_length = _positive(value.get("source_length"))
        digest = value.get("source_sha256")
        extension = value.get("extension")
        if (
            source_length > channel.max_input_bytes
            or type(digest) is not str
            or _DIGEST.fullmatch(digest) is None
            or type(extension) is not str
        ):
            _fail()
        limits = _limits(value.get("limits"), channel)
        if source_length > limits.max_input_bytes:
            _fail()
        try:
            extension = normalize_extension_hint(extension)
        except ReverseConversionError:
            _fail()
        return WorkspaceStageHeader(
            request_id,
            sequence,
            attempt,
            unit,
            create_sequence,
            extension,
            limits,
            source_length,
            digest,
        )
    if operation == WorkspaceOperation.COLLECT:
        expected = {
            "attempt_id",
            "create_sequence",
            "incarnation_id",
            "operation",
            "protocol",
            "receipt_request_id",
            "request_id",
            "sequence",
            "stage_sequence",
            "unit_id",
            "version",
        }
        if set(value) != expected:
            _fail()
        request_id, sequence, attempt, unit, create_sequence = _identity(value)
        return WorkspaceCollectRequest(
            request_id,
            sequence,
            _uuid(value.get("receipt_request_id")),
            _sequence(value.get("stage_sequence")),
            attempt,
            unit,
            create_sequence,
            _uuid(value.get("incarnation_id")),
        )
    _fail()


def bind_workspace_source(
    header: WorkspaceStageHeader, source: bytes
) -> WorkspaceStageRequest:
    """Bind exact raw source bytes to already validated canonical metadata."""

    if (
        type(header) is not WorkspaceStageHeader
        or type(source) is not bytes
        or len(source) != header.source_length
        or f"sha256:{hashlib.sha256(source).hexdigest()}" != header.source_sha256
    ):
        _fail()
    try:
        return WorkspaceStageRequest(
            header.request_id,
            header.sequence,
            header.attempt_id,
            header.unit_id,
            header.create_sequence,
            header.extension,
            header.limits,
            source,
        )
    except ValueError:
        _fail()


def stage_fingerprint(request: WorkspaceStageRequest) -> str:
    """Return an opaque exact-request fingerprint for the volatile replay ledger."""

    encoded = encode_workspace_request(request)
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def receipt_for(
    request: WorkspaceStageRequest, incarnation_id: UUID
) -> WorkspaceStageReceipt:
    if type(incarnation_id) is not UUID:
        _fail()
    return WorkspaceStageReceipt(
        request.request_id,
        request.sequence,
        request.attempt_id,
        request.unit_id,
        request.create_sequence,
        incarnation_id,
    )


def _receipt_mapping(receipt: WorkspaceStageReceipt) -> dict[str, object]:
    if type(receipt) is not WorkspaceStageReceipt:
        _fail()
    return {
        "attempt_id": str(receipt.attempt_id),
        "create_sequence": _sequence(receipt.create_sequence),
        "incarnation_id": str(receipt.incarnation_id),
        "receipt_request_id": str(receipt.request_id),
        "stage_sequence": _sequence(receipt.stage_sequence),
        "unit_id": str(receipt.unit_id),
    }


def encode_workspace_response(
    response: WorkspaceResponse, channel: RuntimeChannelLimits
) -> bytes:
    """Encode a header and exact raw result bytes when successful."""

    if type(channel) is not RuntimeChannelLimits:
        _fail()
    if type(response) is WorkspaceStageReceipt:
        value = _base(response.request_id, WorkspaceOperation.STAGE)
        value.update(outcome="receipt", **_receipt_mapping(response))
        return _frame(value)
    if type(response) is WorkspaceErrorResponse:
        if (
            type(response.operation) is not WorkspaceOperation
            or type(response.category) is not BrokerErrorCategory
        ):
            _fail()
        value = _base(response.request_id, response.operation)
        value.update(outcome="error", category=response.category)
        return _frame(value)
    if not isinstance(
        response,
        (WorkspacePendingResponse, WorkspaceFailureResponse, WorkspaceSuccessResponse),
    ):
        _fail()
    value = _base(response.request_id, WorkspaceOperation.COLLECT)
    value.update(_receipt_mapping(response.receipt))
    payload = b""
    if isinstance(response, WorkspacePendingResponse):
        value["outcome"] = "pending"
    elif isinstance(response, WorkspaceFailureResponse):
        if (
            type(response.category) is not ReverseErrorCategory
            or response.category not in CHILD_FAILURE_CATEGORIES
        ):
            _fail()
        value.update(outcome="failure", category=response.category)
    elif isinstance(response, WorkspaceSuccessResponse):
        if (
            type(response.mode) is not ReverseOutputMode
            or type(response.result) is not bytes
            or not response.result
            or len(response.result) > channel.max_output_bytes
        ):
            _fail()
        payload = response.result
        value.update(
            outcome="success",
            mode=response.mode,
            result_length=len(payload),
            result_sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}",
        )
    else:  # pragma: no cover - exhaustive type narrowing
        _fail()
    return _frame(value) + payload


def _receipt_from(value: dict[str, object]) -> WorkspaceStageReceipt:
    return WorkspaceStageReceipt(
        _uuid(value.get("receipt_request_id")),
        _sequence(value.get("stage_sequence")),
        _uuid(value.get("attempt_id")),
        _uuid(value.get("unit_id")),
        _sequence(value.get("create_sequence")),
        _uuid(value.get("incarnation_id")),
    )


def decode_workspace_response_header(  # noqa: PLR0912
    frame: bytes, channel: RuntimeChannelLimits
) -> tuple[WorkspaceResponse, int, str | None]:
    """Decode a response header and return its expected raw payload contract."""

    if type(channel) is not RuntimeChannelLimits:
        _fail()
    value = _mapping(frame)
    try:
        operation = WorkspaceOperation(value.get("operation"))
    except TypeError, ValueError:
        _fail()
    request_id = _uuid(value.get("request_id"))
    outcome = value.get("outcome")
    common = {"operation", "outcome", "protocol", "request_id", "version"}
    if outcome == "error":
        if set(value) != common | {"category"}:
            _fail()
        try:
            category = BrokerErrorCategory(value.get("category"))
        except TypeError, ValueError:
            _fail()
        return WorkspaceErrorResponse(request_id, operation, category), 0, None
    receipt_keys = {
        "attempt_id",
        "create_sequence",
        "incarnation_id",
        "receipt_request_id",
        "stage_sequence",
        "unit_id",
    }
    if operation is WorkspaceOperation.STAGE and outcome == "receipt":
        if set(value) != common | receipt_keys:
            _fail()
        receipt = _receipt_from(value)
        if receipt.request_id != request_id:
            _fail()
        return receipt, 0, None
    if operation is not WorkspaceOperation.COLLECT:
        _fail()
    receipt = _receipt_from(value)
    if outcome == "pending" and set(value) == common | receipt_keys:
        return WorkspacePendingResponse(request_id, receipt), 0, None
    if outcome == "failure" and set(value) == common | receipt_keys | {"category"}:
        try:
            category = ReverseErrorCategory(value.get("category"))
        except TypeError, ValueError:
            _fail()
        if category not in CHILD_FAILURE_CATEGORIES:
            _fail()
        return WorkspaceFailureResponse(request_id, receipt, category), 0, None
    if outcome == "success" and set(value) == common | receipt_keys | {
        "mode",
        "result_length",
        "result_sha256",
    }:
        length = _positive(value.get("result_length"))
        digest = value.get("result_sha256")
        if (
            length > channel.max_output_bytes
            or type(digest) is not str
            or _DIGEST.fullmatch(digest) is None
        ):
            _fail()
        try:
            mode = ReverseOutputMode(value.get("mode"))
        except TypeError, ValueError:
            _fail()
        return WorkspaceSuccessResponse(request_id, receipt, mode, b""), length, digest
    _fail()


def bind_workspace_result(
    response: WorkspaceResponse, payload: bytes, digest: str | None
) -> WorkspaceResponse:
    """Bind a successful response header to its exact raw bytes."""

    if type(response) is not WorkspaceSuccessResponse:
        if payload or digest is not None:
            _fail()
        return response
    if (
        type(payload) is not bytes
        or f"sha256:{hashlib.sha256(payload).hexdigest()}" != digest
    ):
        _fail()
    return WorkspaceSuccessResponse(
        response.request_id, response.receipt, response.mode, payload
    )


def collect_response(
    request_id: UUID,
    receipt: WorkspaceStageReceipt,
    response: ReverseAttemptResponse | None,
) -> WorkspaceResponse:
    """Map the existing runtime response to the workspace wire model."""

    if response is None:
        return WorkspacePendingResponse(request_id, receipt)
    if type(response) is ReverseAttemptFailure:
        return WorkspaceFailureResponse(request_id, receipt, response.category)
    if type(response) is ReverseAttemptSuccess:
        return WorkspaceSuccessResponse(
            request_id, receipt, response.mode, response.result
        )
    _fail()
