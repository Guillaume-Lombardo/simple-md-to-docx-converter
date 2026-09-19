"""Bounded deterministic TAR exchange for the Podman attempt workspace."""

from __future__ import annotations

import io
import re
import tarfile
from typing import Final

from markweave.broker.command_runner import PodmanRuntimeError
from markweave.reversions.attempt_channel import (
    encode_channel_state,
    encode_request_metadata,
)
from markweave.reversions.models import ReverseAttemptRequest

_TAR_BLOCK_BYTES: Final = 512
_TAR_END_BYTES: Final = 1024
_TAR_PADDING_BYTES: Final = 10_240
_REQUEST_COMMIT = b"committed\n"


def _build_request_archive(request: ReverseAttemptRequest) -> bytes:
    entries = (
        ("source.bin", request.source),
        ("request.json", encode_request_metadata(request)),
        ("response.state", encode_channel_state(request.attempt_id, "pending")),
        ("request.commit", _REQUEST_COMMIT),
    )
    output = io.BytesIO()
    try:
        with tarfile.open(
            fileobj=output, mode="w", format=tarfile.USTAR_FORMAT
        ) as archive:
            for name, content in entries:
                information = tarfile.TarInfo(name)
                information.size = len(content)
                information.mode = 0o440
                information.uid = 0
                information.gid = 0
                information.mtime = 0
                information.uname = ""
                information.gname = ""
                archive.addfile(information, io.BytesIO(content))
    except (OSError, tarfile.TarError, ValueError) as error:
        raise PodmanRuntimeError("Podman workspace archive failed") from error
    encoded = output.getvalue()
    used = sum(
        _TAR_BLOCK_BYTES
        + ((len(content) + _TAR_BLOCK_BYTES - 1) // _TAR_BLOCK_BYTES) * _TAR_BLOCK_BYTES
        for _, content in entries
    )
    return encoded[: used + _TAR_END_BYTES]


def _tar_output_ceiling(content_bytes: int) -> int:
    if type(content_bytes) is not int or content_bytes < 0:
        raise PodmanRuntimeError("Podman workspace bound is invalid")
    blocks = (content_bytes + _TAR_BLOCK_BYTES - 1) // _TAR_BLOCK_BYTES
    records = (blocks + 3 + 19) // 20
    return max(_TAR_PADDING_BYTES, records * _TAR_PADDING_BYTES)


def _read_single_file_archive(value: bytes, expected_name: str, maximum: int) -> bytes:
    if (
        type(value) is not bytes
        or type(expected_name) is not str
        or expected_name not in {"response.state", "response.json", "result.bin"}
        or type(maximum) is not int
        or maximum < 0
        or len(value) > _tar_output_ceiling(maximum)
        or len(value) % _TAR_BLOCK_BYTES
        or len(value) < _TAR_END_BYTES
        or value[-_TAR_END_BYTES:] != b"\0" * _TAR_END_BYTES
    ):
        raise PodmanRuntimeError("Podman workspace archive is invalid")
    try:
        with tarfile.open(fileobj=io.BytesIO(value), mode="r:") as archive:
            members = archive.getmembers()
            if len(members) != 1:
                raise PodmanRuntimeError("Podman workspace archive is invalid")
            member = members[0]
            header = value[:_TAR_BLOCK_BYTES]
            encoded_name = expected_name.encode("ascii")
            if (
                member.name != expected_name
                or member.type != tarfile.REGTYPE
                or member.linkname
                or member.pax_headers
                or member.size > maximum
                or member.offset != 0
                or member.offset_data != _TAR_BLOCK_BYTES
                or header[:100] != encoded_name.ljust(100, b"\0")
                or re.fullmatch(rb"[0-7]{7}\0", header[100:108]) is None
                or re.fullmatch(rb"[0-7]{7}\0", header[108:116]) is None
                or re.fullmatch(rb"[0-7]{7}\0", header[116:124]) is None
                or re.fullmatch(rb"[0-7]{11}\0", header[124:136]) is None
                or re.fullmatch(rb"[0-7]{11}\0", header[136:148]) is None
                or re.fullmatch(rb"[0-7]{6}\0 ", header[148:156]) is None
                or header[156:157] != tarfile.REGTYPE
                or header[157:257] != b"\0" * 100
                or header[257:263] != b"ustar\0"
                or header[263:265] != b"00"
                or header[345:500] != b"\0" * 155
                or header[500:512] != b"\0" * 12
            ):
                raise PodmanRuntimeError("Podman workspace archive is invalid")
            content_end = (
                member.offset_data
                + ((member.size + _TAR_BLOCK_BYTES - 1) // _TAR_BLOCK_BYTES)
                * _TAR_BLOCK_BYTES
            )
            if (
                any(value[member.offset_data + member.size : content_end])
                or len(value) - content_end < _TAR_END_BYTES
                or any(value[content_end:])
            ):
                raise PodmanRuntimeError("Podman workspace archive is invalid")
            stream = archive.extractfile(member)
            if stream is None:
                raise PodmanRuntimeError("Podman workspace archive is invalid")
            content = stream.read(maximum + 1)
            if len(content) != member.size or len(content) > maximum:
                raise PodmanRuntimeError("Podman workspace archive is invalid")
            return content
    except (OSError, EOFError, tarfile.TarError) as error:
        raise PodmanRuntimeError("Podman workspace archive is invalid") from error
