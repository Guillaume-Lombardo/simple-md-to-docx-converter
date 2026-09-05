"""Strict fixed-workspace IPC for one disposable reverse-attempt child."""

from __future__ import annotations

import json
import os
import stat
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from markweave.reversions.errors import (
    ReverseConversionError,
    ReverseErrorCategory,
    reject,
)
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptRequest,
    ReverseAttemptResponse,
    ReverseAttemptSuccess,
    ReverseContentLimits,
    ReverseOutputMode,
)

PROTOCOL_NAME = "markweave-reverse-attempt"
PROTOCOL_VERSION = 1
MAX_METADATA_BYTES = 4096

REQUEST_METADATA_PATH = Path("/work/request.json")
SOURCE_PATH = Path("/work/source.bin")
RESULT_PATH = Path("/work/result.bin")
RESPONSE_METADATA_PATH = Path("/work/response.json")
REQUEST_COMMIT_PATH = Path("/work/request.commit")
RESPONSE_STATE_PATH = Path("/work/response.state")

_REQUEST_METADATA_TEMP_PATH = Path("/work/.request.json.tmp")
_SOURCE_TEMP_PATH = Path("/work/.source.bin.tmp")
_RESULT_TEMP_PATH = Path("/work/.result.bin.tmp")
_RESPONSE_METADATA_TEMP_PATH = Path("/work/.response.json.tmp")
_REQUEST_COMMIT_TEMP_PATH = Path("/work/.request.commit.tmp")
_RESPONSE_STATE_TEMP_PATH = Path("/work/.response.state.tmp")

_CHILD_FAILURE_CATEGORIES = frozenset(
    {
        ReverseErrorCategory.UNSUPPORTED,
        ReverseErrorCategory.MALFORMED,
        ReverseErrorCategory.ENCRYPTED,
        ReverseErrorCategory.RESOURCE_LIMIT,
        ReverseErrorCategory.NEEDS_OCR,
        ReverseErrorCategory.ASSET_INVALID,
        ReverseErrorCategory.PROTOCOL_ERROR,
    }
)
_CONTENT_LIMIT_FIELDS = frozenset(
    {
        "max_input_bytes",
        "max_output_bytes",
        "max_image_source_bytes",
        "max_image_width_pixels",
        "max_image_height_pixels",
        "max_image_pixels",
        "max_svg_elements",
        "max_svg_depth",
        "max_asset_count",
        "max_total_asset_source_bytes",
        "max_total_asset_output_bytes",
        "max_markdown_bytes",
        "max_package_bytes",
    }
)


@dataclass(frozen=True, slots=True)
class AttemptChannelLimits:
    """T71-owned document ceilings enforced before allocating file contents."""

    max_input_bytes: int
    max_output_bytes: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (self.max_input_bytes, self.max_output_bytes)
        ):
            raise ValueError("Attempt channel byte limits must be positive")


def _canonical_metadata(value: dict[str, object]) -> bytes:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    if len(encoded) + 1 > MAX_METADATA_BYTES:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    return encoded + b"\n"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _decode_metadata(encoded: bytes) -> dict[str, object]:
    if len(encoded) > MAX_METADATA_BYTES or not encoded.endswith(b"\n"):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    try:
        value = json.loads(
            encoded.decode("ascii"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except UnicodeDecodeError, ValueError, json.JSONDecodeError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    if not isinstance(value, dict) or _canonical_metadata(value) != encoded:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    return value


def _require_keys(metadata: dict[str, object], expected: frozenset[str]) -> None:
    if metadata.keys() != expected:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)


def _uuid(value: object) -> UUID:
    if not isinstance(value, str):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    try:
        parsed = UUID(value)
    except ValueError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    if str(parsed) != value:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    return parsed


def _protocol_header(metadata: dict[str, object], message_type: str) -> None:
    if (
        metadata.get("protocol") != PROTOCOL_NAME
        or metadata.get("version") != PROTOCOL_VERSION
        or metadata.get("type") != message_type
    ):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)


def _encode_limits(limits: ReverseContentLimits) -> dict[str, int]:
    return {field: getattr(limits, field) for field in sorted(_CONTENT_LIMIT_FIELDS)}


def _decode_limits(value: object) -> ReverseContentLimits:
    if not isinstance(value, dict) or value.keys() != _CONTENT_LIMIT_FIELDS:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    if any(type(item) is not int for item in value.values()):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    try:
        return ReverseContentLimits(**value)
    except TypeError, ValueError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)


def encode_request_metadata(request: ReverseAttemptRequest) -> bytes:
    """Encode the request's fixed, content-free canonical metadata."""

    return _canonical_metadata(
        {
            "attempt_id": str(request.attempt_id),
            "extension": request.extension,
            "limits": _encode_limits(request.limits),
            "protocol": PROTOCOL_NAME,
            "type": "request",
            "version": PROTOCOL_VERSION,
        }
    )


def decode_request_metadata(encoded: bytes, source: bytes) -> ReverseAttemptRequest:
    """Decode a request while rejecting non-canonical or caller-extended policy."""

    metadata = _decode_metadata(encoded)
    _require_keys(
        metadata,
        frozenset(
            {
                "attempt_id",
                "extension",
                "limits",
                "protocol",
                "type",
                "version",
            }
        ),
    )
    _protocol_header(metadata, "request")
    try:
        extension = metadata["extension"]
        if not isinstance(extension, str) or not source:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        limits = _decode_limits(metadata["limits"])
    except KeyError, ValueError, ReverseConversionError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    return ReverseAttemptRequest(
        attempt_id=_uuid(metadata["attempt_id"]),
        extension=extension,
        limits=limits,
        source=source,
    )


def encode_response_metadata(response: ReverseAttemptResponse) -> bytes:
    """Encode fixed canonical success or content-free failure metadata."""

    if isinstance(response, ReverseAttemptFailure):
        if response.category not in _CHILD_FAILURE_CATEGORIES:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        return _canonical_metadata(
            {
                "attempt_id": str(response.attempt_id),
                "category": response.category,
                "protocol": PROTOCOL_NAME,
                "type": "failure",
                "version": PROTOCOL_VERSION,
            }
        )
    return _canonical_metadata(
        {
            "attempt_id": str(response.attempt_id),
            "mode": response.mode,
            "protocol": PROTOCOL_NAME,
            "type": "success",
            "version": PROTOCOL_VERSION,
        }
    )


def encode_channel_state(attempt_id: UUID, state: str) -> bytes:
    """Encode the fixed attempt-bound workspace commit state."""

    if type(attempt_id) is not UUID or state not in {"pending", "complete"}:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    return _canonical_metadata(
        {
            "attempt_id": str(attempt_id),
            "protocol": PROTOCOL_NAME,
            "state": state,
            "type": "channel_state",
            "version": PROTOCOL_VERSION,
        }
    )


def decode_channel_state(encoded: bytes, expected_attempt_id: UUID) -> str:
    """Decode an exact attempt-bound pending or complete marker."""

    metadata = _decode_metadata(encoded)
    _require_keys(
        metadata,
        frozenset({"attempt_id", "protocol", "state", "type", "version"}),
    )
    _protocol_header(metadata, "channel_state")
    state = metadata.get("state")
    if (
        _uuid(metadata.get("attempt_id")) != expected_attempt_id
        or type(state) is not str
        or state not in {"pending", "complete"}
    ):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    return state


def decode_response_metadata(
    encoded: bytes, result: bytes | None
) -> ReverseAttemptResponse:
    """Decode a response and require result bytes only for success."""

    metadata = _decode_metadata(encoded)
    message_type = metadata.get("type")
    if message_type == "success":
        _require_keys(
            metadata,
            frozenset({"attempt_id", "mode", "protocol", "type", "version"}),
        )
        _protocol_header(metadata, "success")
        if result is None:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        try:
            mode_value = metadata["mode"]
            if not isinstance(mode_value, str):
                reject(ReverseErrorCategory.PROTOCOL_ERROR)
            mode = ReverseOutputMode(mode_value)
        except KeyError, ValueError:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        return ReverseAttemptSuccess(
            attempt_id=_uuid(metadata["attempt_id"]), mode=mode, result=result
        )
    if message_type == "failure":
        _require_keys(
            metadata,
            frozenset({"attempt_id", "category", "protocol", "type", "version"}),
        )
        _protocol_header(metadata, "failure")
        if result is not None:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        try:
            category_value = metadata["category"]
            if not isinstance(category_value, str):
                reject(ReverseErrorCategory.PROTOCOL_ERROR)
            category = ReverseErrorCategory(category_value)
        except KeyError, ValueError:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        if category not in _CHILD_FAILURE_CATEGORIES:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        return ReverseAttemptFailure(
            attempt_id=_uuid(metadata["attempt_id"]), category=category
        )
    return reject(ReverseErrorCategory.PROTOCOL_ERROR)


def _read_bounded(path: Path, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        if details.st_size > maximum:
            reject(ReverseErrorCategory.RESOURCE_LIMIT)
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        content = b"".join(chunks)
        if len(content) > maximum:
            reject(ReverseErrorCategory.RESOURCE_LIMIT)
        return content
    except OSError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, temporary_path: Path, content: bytes) -> None:
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    else:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary_path, flags, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(OSError):
            temporary_path.unlink(missing_ok=True)


def _atomic_replace(path: Path, temporary_path: Path, content: bytes) -> None:
    try:
        metadata = path.lstat()
    except OSError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    if not stat.S_ISREG(metadata.st_mode):
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary_path, flags, 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary_path, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(OSError):
            temporary_path.unlink(missing_ok=True)


def write_request(request: ReverseAttemptRequest, limits: AttemptChannelLimits) -> None:
    """Atomically stage the fixed source then commit its request metadata."""

    if (
        len(request.source) > limits.max_input_bytes
        or request.limits.max_input_bytes > limits.max_input_bytes
        or request.limits.max_output_bytes > limits.max_output_bytes
    ):
        reject(ReverseErrorCategory.RESOURCE_LIMIT)
    _atomic_write(SOURCE_PATH, _SOURCE_TEMP_PATH, request.source)
    _atomic_write(
        REQUEST_METADATA_PATH,
        _REQUEST_METADATA_TEMP_PATH,
        encode_request_metadata(request),
    )
    _atomic_write(
        RESPONSE_STATE_PATH,
        _RESPONSE_STATE_TEMP_PATH,
        encode_channel_state(request.attempt_id, "pending"),
    )
    _atomic_write(REQUEST_COMMIT_PATH, _REQUEST_COMMIT_TEMP_PATH, b"committed\n")


def read_request(limits: AttemptChannelLimits) -> ReverseAttemptRequest:
    """Read one fixed request with strict metadata and source bounds."""

    if _read_bounded(REQUEST_COMMIT_PATH, len(b"committed\n")) != b"committed\n":
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    metadata = _read_bounded(REQUEST_METADATA_PATH, MAX_METADATA_BYTES)
    source = _read_bounded(SOURCE_PATH, limits.max_input_bytes)
    request = decode_request_metadata(metadata, source)
    if (
        request.limits.max_input_bytes > limits.max_input_bytes
        or request.limits.max_output_bytes > limits.max_output_bytes
    ):
        reject(ReverseErrorCategory.RESOURCE_LIMIT)
    return request


def wait_for_request(limits: AttemptChannelLimits) -> ReverseAttemptRequest:
    """Wait for the broker's final request marker under the runtime deadline."""

    while True:
        try:
            metadata = REQUEST_COMMIT_PATH.lstat()
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        except OSError:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        if not stat.S_ISREG(metadata.st_mode):
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        return read_request(limits)


def write_response(
    response: ReverseAttemptResponse, limits: AttemptChannelLimits
) -> None:
    """Atomically stage a result, when present, then commit response metadata."""

    if (
        isinstance(response, ReverseAttemptSuccess)
        and len(response.result) > limits.max_output_bytes
    ):
        reject(ReverseErrorCategory.RESOURCE_LIMIT)
    state = _read_bounded(RESPONSE_STATE_PATH, MAX_METADATA_BYTES)
    if decode_channel_state(state, response.attempt_id) != "pending":
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    if isinstance(response, ReverseAttemptSuccess):
        _atomic_write(RESULT_PATH, _RESULT_TEMP_PATH, response.result)
    else:
        try:
            RESULT_PATH.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        else:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
    _atomic_write(
        RESPONSE_METADATA_PATH,
        _RESPONSE_METADATA_TEMP_PATH,
        encode_response_metadata(response),
    )
    _atomic_replace(
        RESPONSE_STATE_PATH,
        _RESPONSE_STATE_TEMP_PATH,
        encode_channel_state(response.attempt_id, "complete"),
    )


def read_response(
    limits: AttemptChannelLimits, expected_attempt_id: UUID
) -> ReverseAttemptResponse:
    """Read one fixed response bound to the supervisor's expected attempt."""

    if type(expected_attempt_id) is not UUID:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)

    state = _read_bounded(RESPONSE_STATE_PATH, MAX_METADATA_BYTES)
    if decode_channel_state(state, expected_attempt_id) != "complete":
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    metadata = _read_bounded(RESPONSE_METADATA_PATH, MAX_METADATA_BYTES)
    decoded = _decode_metadata(metadata)
    message_type = decoded.get("type")
    if message_type == "success":
        result = _read_bounded(RESULT_PATH, limits.max_output_bytes)
    elif message_type == "failure":
        try:
            RESULT_PATH.lstat()
        except FileNotFoundError:
            result = None
        except OSError:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
        else:
            reject(ReverseErrorCategory.PROTOCOL_ERROR)
    else:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    response = decode_response_metadata(metadata, result)
    if response.attempt_id != expected_attempt_id:
        reject(ReverseErrorCategory.PROTOCOL_ERROR)
    return response
