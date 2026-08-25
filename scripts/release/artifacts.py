"""Create and verify deterministic integrity metadata for Python artifacts."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import stat
import struct
import tarfile
import tomllib
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from email.message import Message
from email.parser import BytesParser
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import IO, TYPE_CHECKING, Any, BinaryIO

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

MANIFEST_NAME = "release-integrity.json"
MANIFEST_SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 64 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_CENTRAL_DIRECTORY_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
MAX_COMPRESSION_RATIO = 500
READ_CHUNK_BYTES = 1024 * 1024
RECORD = "RECORD"
PUBLIC_IMPORT_PACKAGE = "markweave"
LEGACY_IMPORT_PACKAGE = "md_converter"
ARTIFACT_COUNT = 2
RECORD_FIELD_COUNT = 3
INSTALL_IMPORT_PATH_PARTS = 3
SHA256 = re.compile(r"[0-9a-f]{64}")
SAFE_BASENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
EOCD_SIGNATURE = b"PK\x05\x06"
ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
EOCD_MIN_BYTES = 22
EOCD_MAX_COMMENT_BYTES = 65_535
ZIP64_LOCATOR_BYTES = 20
ZIP64_EOCD_MIN_BYTES = 56
MAX_ZIP64_EOCD_BYTES = 4096
ZIP16_SENTINEL = 0xFFFF
ZIP32_SENTINEL = 0xFFFFFFFF


class ArtifactError(ValueError):
    """An artifact or its integrity manifest is invalid."""


@dataclass(frozen=True)
class ArtifactSet:
    """The single wheel and source distribution in a release directory."""

    wheel: Path
    sdist: Path
    integrity: tuple[tuple[str, str], ...] = ()

    @property
    def files(self) -> tuple[Path, Path]:
        """Return artifacts in deterministic filename order."""
        first, second = sorted((self.wheel, self.sdist))
        return first, second

    def sha256_for(self, path: Path) -> str:
        """Return the manifest-bound digest for an artifact."""
        digests = dict(self.integrity)
        try:
            return digests[path.name]
        except KeyError as error:
            raise ArtifactError(f"no verified digest for {path.name}") from error


def normalized_name(value: str) -> str:
    """Return the normalized Python distribution name."""
    return re.sub(r"[-_.]+", "-", value).lower()


def _distribution_component(value: str) -> str:
    return normalized_name(value).replace("-", "_")


def _is_legacy_import_entry(value: str) -> bool:
    return value in {LEGACY_IMPORT_PACKAGE, f"{LEGACY_IMPORT_PACKAGE}.py"} or (
        value.startswith(f"{LEGACY_IMPORT_PACKAGE}.")
        and value.endswith((".so", ".pyd"))
    )


def _wheel_installs_legacy_import(name: str, *, data_directory: str) -> bool:
    parts = PurePosixPath(name).parts
    if parts[0] == data_directory:
        if len(parts) < INSTALL_IMPORT_PATH_PARTS or parts[1] not in {
            "purelib",
            "platlib",
        }:
            return False
        return _is_legacy_import_entry(parts[2])
    return _is_legacy_import_entry(parts[0])


def _safe_manifest_name(value: str) -> str:
    if (
        not SAFE_BASENAME.fullmatch(value)
        or value in {".", ".."}
        or Path(value).name != value
        or PureWindowsPath(value).name != value
    ):
        raise ArtifactError(f"unsafe integrity manifest name: {value!r}")
    return value


def _safe_archive_path(value: str) -> PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise ArtifactError(f"unsafe archive path: {value!r}")
    path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)
    if path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise ArtifactError(f"unsafe archive path: {value!r}")
    if str(path) != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactError(f"unsafe archive path: {value!r}")
    return path


def _require_unique_paths(paths: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in paths:
        key = value.rstrip("/")
        _safe_archive_path(key)
        if key in seen:
            raise ArtifactError(f"duplicate archive path: {key}")
        seen.add(key)
        result.append(key)
    return result


@contextmanager
def _open_regular(path: Path, *, max_bytes: int, label: str) -> Iterator[BinaryIO]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ArtifactError(f"cannot open {label}: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ArtifactError(f"{label} is not a regular file")
        if metadata.st_size > max_bytes:
            raise ArtifactError(f"{label} exceeds the size limit")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            yield stream
    finally:
        os.close(descriptor)


def _bounded_read(stream: IO[bytes], *, limit: int, label: str) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ArtifactError(f"{label} exceeds the size limit")
    return data


def file_sha256(path: Path, *, max_bytes: int = MAX_ARTIFACT_BYTES) -> str:
    """Hash one regular non-symlink file while enforcing a byte limit."""
    digest = hashlib.sha256()
    total = 0
    with _open_regular(path, max_bytes=max_bytes, label=path.name) as stream:
        while chunk := stream.read(READ_CHUNK_BYTES):
            total += len(chunk)
            if total > max_bytes:
                raise ArtifactError(f"{path.name} exceeds the size limit")
            digest.update(chunk)
    return digest.hexdigest()


def _require_artifact(path: Path) -> None:
    with _open_regular(path, max_bytes=MAX_ARTIFACT_BYTES, label=path.name):
        pass


def discover_artifacts(directory: Path) -> ArtifactSet:
    """Discover exactly one regular wheel and one source distribution."""
    if not directory.is_dir() or directory.is_symlink():
        raise ArtifactError(f"invalid artifact directory: {directory}")
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1:
        raise ArtifactError(f"expected exactly one wheel, found {len(wheels)}")
    if len(sdists) != 1:
        raise ArtifactError(f"expected exactly one sdist, found {len(sdists)}")
    for path in (*wheels, *sdists):
        _require_artifact(path)
    return ArtifactSet(wheel=wheels[0], sdist=sdists[0])


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()


def create_manifest(
    directory: Path,
    *,
    expected_name: str,
    expected_version: str,
    manifest_name: str = MANIFEST_NAME,
) -> Path:
    """Write canonical integrity metadata for the two release artifacts."""
    safe_name = _safe_manifest_name(manifest_name)
    artifacts = discover_artifacts(directory)
    document = {
        "algorithm": "sha256",
        "artifacts": [
            {
                "filename": path.name,
                "sha256": file_sha256(path),
                "size": path.stat().st_size,
            }
            for path in artifacts.files
        ],
        "name": expected_name,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "version": expected_version,
    }
    encoded = _canonical_json(document)
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ArtifactError("integrity manifest exceeds the size limit")
    target = directory / safe_name
    try:
        with target.open("xb") as stream:
            stream.write(encoded)
    except OSError as error:
        raise ArtifactError(f"cannot create integrity manifest: {error}") from error
    return target


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        with _open_regular(
            path, max_bytes=MAX_MANIFEST_BYTES, label="integrity manifest"
        ) as stream:
            raw = _bounded_read(
                stream, limit=MAX_MANIFEST_BYTES, label="integrity manifest"
            )
        document: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArtifactError(f"invalid integrity manifest: {error}") from error
    if not isinstance(document, dict) or raw != _canonical_json(document):
        raise ArtifactError("integrity manifest is not canonical JSON")
    required = {"algorithm", "artifacts", "name", "schema_version", "version"}
    if set(document) != required:
        raise ArtifactError("integrity manifest has unexpected fields")
    return document


def _verify_manifest(
    path: Path,
    artifacts: ArtifactSet,
    *,
    expected_name: str,
    expected_version: str,
) -> tuple[tuple[str, str], ...]:
    document = _load_manifest(path)
    if document["schema_version"] != MANIFEST_SCHEMA_VERSION:
        raise ArtifactError("unsupported integrity manifest schema")
    if document["algorithm"] != "sha256":
        raise ArtifactError("integrity manifest must use sha256")
    if document["name"] != expected_name or document["version"] != expected_version:
        raise ArtifactError("integrity manifest identifies another release")
    entries = document["artifacts"]
    if not isinstance(entries, list) or len(entries) != ARTIFACT_COUNT:
        raise ArtifactError("integrity manifest must contain exactly two artifacts")
    actual_names = [artifact.name for artifact in artifacts.files]
    if [
        entry.get("filename") for entry in entries if isinstance(entry, dict)
    ] != actual_names:
        raise ArtifactError(
            "integrity manifest artifact list does not match the directory"
        )
    integrity: list[tuple[str, str]] = []
    for entry, artifact in zip(entries, artifacts.files, strict=True):
        if not isinstance(entry, dict) or set(entry) != {"filename", "sha256", "size"}:
            raise ArtifactError("invalid integrity manifest artifact entry")
        digest = entry["sha256"]
        size = entry["size"]
        if (
            not isinstance(digest, str)
            or SHA256.fullmatch(digest) is None
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or size > MAX_ARTIFACT_BYTES
        ):
            raise ArtifactError("invalid integrity manifest artifact values")
        if size != artifact.stat().st_size or digest != file_sha256(artifact):
            raise ArtifactError(f"integrity check failed for {artifact.name}")
        integrity.append((artifact.name, digest))
    return tuple(integrity)


def _metadata(data: bytes, label: str) -> Message:
    if len(data) > MAX_METADATA_BYTES:
        raise ArtifactError(f"{label} exceeds the metadata size limit")
    try:
        return BytesParser().parsebytes(data)
    except Exception as error:  # email parser exposes no narrower exception contract
        raise ArtifactError(f"invalid {label}: {error}") from error


def _require_identity(
    metadata: Message, *, expected_name: str, expected_version: str, label: str
) -> None:
    name = metadata.get("Name")
    version = metadata.get("Version")
    if name is None or normalized_name(name) != normalized_name(expected_name):
        raise ArtifactError(f"{label} has an unexpected Name")
    if version != expected_version:
        raise ArtifactError(f"{label} has an unexpected Version")


def _wheel_identity(path: Path, expected_name: str, expected_version: str) -> None:
    parts = path.name.removesuffix(".whl").split("-")
    if len(parts) not in {5, 6}:
        raise ArtifactError("invalid wheel filename")
    if normalized_name(parts[0]) != normalized_name(expected_name):
        raise ArtifactError("wheel filename has an unexpected distribution name")
    if parts[1] != expected_version:
        raise ArtifactError("wheel filename has an unexpected version")


def _read_at(stream: BinaryIO, *, offset: int, size: int, label: str) -> bytes:
    if offset < 0:
        raise ArtifactError(f"invalid wheel {label} offset")
    stream.seek(offset)
    data = stream.read(size)
    if len(data) != size:
        raise ArtifactError(f"truncated wheel {label}")
    return data


def _zip64_directory(
    stream: BinaryIO, *, eocd_offset: int
) -> tuple[int, int, int, int]:
    locator = _read_at(
        stream,
        offset=eocd_offset - ZIP64_LOCATOR_BYTES,
        size=ZIP64_LOCATOR_BYTES,
        label="ZIP64 locator",
    )
    signature, zip64_disk, zip64_offset, disk_count = struct.unpack("<4sLQL", locator)
    if signature != ZIP64_LOCATOR_SIGNATURE or zip64_disk != 0 or disk_count != 1:
        raise ArtifactError("invalid or multidisk wheel ZIP64 locator")
    header = _read_at(
        stream,
        offset=zip64_offset,
        size=ZIP64_EOCD_MIN_BYTES,
        label="ZIP64 EOCD",
    )
    if header[:4] != ZIP64_EOCD_SIGNATURE:
        raise ArtifactError("invalid wheel ZIP64 EOCD signature")
    record_size = struct.unpack_from("<Q", header, 4)[0]
    complete_size = record_size + 12
    if complete_size < ZIP64_EOCD_MIN_BYTES or complete_size > MAX_ZIP64_EOCD_BYTES:
        raise ArtifactError("wheel ZIP64 EOCD exceeds its size limit")
    if zip64_offset + complete_size != eocd_offset - ZIP64_LOCATOR_BYTES:
        raise ArtifactError("wheel ZIP64 EOCD has an invalid bounded region")
    (
        _version_made,
        _version_needed,
        disk_number,
        directory_disk,
        entries_on_disk,
        entry_count,
        directory_size,
        directory_offset,
    ) = struct.unpack_from("<2H2L4Q", header, 12)
    if disk_number != 0 or directory_disk != 0 or entries_on_disk != entry_count:
        raise ArtifactError("multidisk wheel archives are forbidden")
    return entry_count, directory_size, directory_offset, zip64_offset


def _find_eocd(tail: bytes) -> int:
    search_end = len(tail)
    while search_end:
        relative_offset = tail.rfind(EOCD_SIGNATURE, 0, search_end)
        if relative_offset < 0:
            break
        if len(tail) - relative_offset >= EOCD_MIN_BYTES:
            comment_size = struct.unpack_from("<H", tail, relative_offset + 20)[0]
            if relative_offset + EOCD_MIN_BYTES + comment_size == len(tail):
                return relative_offset
        search_end = relative_offset
    raise ArtifactError("wheel has no bounded EOCD record")


def _prevalidate_zip_directory(path: Path) -> None:
    """Bound EOCD and central-directory resources before ZipFile allocates."""
    with _open_regular(path, max_bytes=MAX_ARTIFACT_BYTES, label=path.name) as stream:
        stream.seek(0, os.SEEK_END)
        archive_size = stream.tell()
        tail_size = min(archive_size, EOCD_MIN_BYTES + EOCD_MAX_COMMENT_BYTES)
        tail_offset = archive_size - tail_size
        tail = _read_at(
            stream, offset=tail_offset, size=tail_size, label="EOCD search window"
        )
        relative_offset = _find_eocd(tail)
        eocd_offset = tail_offset + relative_offset
        (
            _signature,
            disk_number,
            directory_disk,
            entries_on_disk,
            entry_count,
            directory_size,
            directory_offset,
            _comment_size,
        ) = struct.unpack_from("<4s4H2LH", tail, relative_offset)
        sentinel = ZIP16_SENTINEL in (
            entry_count,
            entries_on_disk,
        ) or ZIP32_SENTINEL in (directory_size, directory_offset)
        directory_end_limit = eocd_offset
        if disk_number != 0 or directory_disk != 0:
            raise ArtifactError("multidisk wheel archives are forbidden")
        if sentinel:
            (
                entry_count,
                directory_size,
                directory_offset,
                directory_end_limit,
            ) = _zip64_directory(stream, eocd_offset=eocd_offset)
        elif entries_on_disk != entry_count:
            raise ArtifactError("multidisk wheel archives are forbidden")
        if entry_count > MAX_ARCHIVE_MEMBERS:
            raise ArtifactError("wheel has too many central-directory entries")
        if directory_size > MAX_CENTRAL_DIRECTORY_BYTES:
            raise ArtifactError("wheel central directory exceeds its size limit")
        if entry_count and directory_size < entry_count * 46:
            raise ArtifactError("wheel central directory is structurally too small")
        if directory_offset + directory_size > directory_end_limit:
            raise ArtifactError("wheel central directory exceeds its bounded region")


def _zip_member_bytes(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_METADATA_BYTES:
        raise ArtifactError(f"{name} exceeds the metadata size limit")
    with archive.open(info) as stream:
        return _bounded_read(stream, limit=MAX_METADATA_BYTES, label=name)


def _zip_member_digest(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    total = 0
    with archive.open(info) as stream:
        while chunk := stream.read(READ_CHUNK_BYTES):
            total += len(chunk)
            if total > info.file_size or total > MAX_MEMBER_BYTES:
                raise ArtifactError(f"wheel member exceeds its limit: {info.filename}")
            digest.update(chunk)
    if total != info.file_size:
        raise ArtifactError(f"wheel member size changed: {info.filename}")
    return digest.hexdigest()


def _validate_zip_resources(infos: list[zipfile.ZipInfo]) -> None:
    if len(infos) > MAX_ARCHIVE_MEMBERS:
        raise ArtifactError("wheel has too many members")
    total_size = 0
    total_compressed = 0
    for info in infos:
        if info.file_size > MAX_MEMBER_BYTES:
            raise ArtifactError(f"wheel member exceeds its limit: {info.filename}")
        total_size += info.file_size
        total_compressed += info.compress_size
        if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ArtifactError("wheel exceeds the uncompressed size limit")
    if total_size > max(total_compressed, 1) * MAX_COMPRESSION_RATIO:
        raise ArtifactError("wheel exceeds the compression ratio limit")


def _verify_record(
    archive: zipfile.ZipFile, record_name: str, names: list[str]
) -> None:
    try:
        rows = list(
            csv.reader(io.StringIO(_zip_member_bytes(archive, record_name).decode()))
        )
    except (UnicodeDecodeError, csv.Error) as error:
        raise ArtifactError(f"invalid wheel RECORD: {error}") from error
    if any(len(row) != RECORD_FIELD_COUNT for row in rows):
        raise ArtifactError("wheel RECORD rows must have three fields")
    record_paths = _require_unique_paths(row[0] for row in rows)
    if set(record_paths) != set(names):
        raise ArtifactError("wheel RECORD does not exactly describe archive members")
    for member_name, encoded_hash, encoded_size in rows:
        if member_name == record_name:
            if encoded_hash or encoded_size:
                raise ArtifactError("wheel RECORD must not hash itself")
            continue
        info = archive.getinfo(member_name)
        raw_digest = bytes.fromhex(_zip_member_digest(archive, info))
        expected_hash = base64.urlsafe_b64encode(raw_digest).rstrip(b"=").decode()
        if encoded_hash != f"sha256={expected_hash}" or encoded_size != str(
            info.file_size
        ):
            raise ArtifactError(f"wheel RECORD integrity failed for {member_name}")


def verify_wheel(path: Path, *, expected_name: str, expected_version: str) -> None:
    """Verify the wheel filename, metadata, contents, resources, and RECORD."""
    _wheel_identity(path, expected_name, expected_version)
    _require_artifact(path)
    _prevalidate_zip_directory(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            _validate_zip_resources(infos)
            names = _require_unique_paths(info.filename for info in infos)
            for info in infos:
                mode = info.external_attr >> 16
                if info.flag_bits & 1:
                    raise ArtifactError("encrypted wheel members are forbidden")
                file_type = stat.S_IFMT(mode)
                if file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    raise ArtifactError(f"non-regular wheel member: {info.filename}")
            dist_info = (
                f"{_distribution_component(expected_name)}-{expected_version}.dist-info"
            )
            metadata_name = f"{dist_info}/METADATA"
            wheel_name = f"{dist_info}/WHEEL"
            record_name = f"{dist_info}/{RECORD}"
            data_directory = (
                f"{_distribution_component(expected_name)}-{expected_version}.data"
            )
            required = {
                f"{PUBLIC_IMPORT_PACKAGE}/__init__.py",
                metadata_name,
                wheel_name,
                record_name,
            }
            missing = required.difference(names)
            if missing:
                raise ArtifactError(
                    f"wheel is missing required members: {sorted(missing)}"
                )
            if any(
                _wheel_installs_legacy_import(name, data_directory=data_directory)
                for name in names
            ):
                raise ArtifactError("wheel contains the legacy public import package")
            if any(
                "__pycache__" in PurePosixPath(name).parts or name.endswith(".pyc")
                for name in names
            ):
                raise ArtifactError("wheel contains generated Python bytecode")
            _require_identity(
                _metadata(_zip_member_bytes(archive, metadata_name), "wheel METADATA"),
                expected_name=expected_name,
                expected_version=expected_version,
                label="wheel METADATA",
            )
            wheel_metadata = _metadata(
                _zip_member_bytes(archive, wheel_name), "wheel WHEEL metadata"
            )
            tags = wheel_metadata.get_all("Tag", [])
            filename_tag = "-".join(path.name.removesuffix(".whl").split("-")[-3:])
            if (
                wheel_metadata.get("Wheel-Version") is None
                or not tags
                or filename_tag not in tags
            ):
                raise ArtifactError("wheel WHEEL metadata is incomplete")
            _verify_record(archive, record_name, names)
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError) as error:
        raise ArtifactError(f"invalid wheel archive: {error}") from error


def _scan_sdist(
    path: Path, *, expected_stem: str
) -> tuple[list[str], dict[str, bytes]]:
    names: list[str] = []
    metadata_members: dict[str, bytes] = {}
    total_size = 0
    compressed_size = path.stat().st_size
    required_metadata = {
        f"{expected_stem}/PKG-INFO",
        f"{expected_stem}/pyproject.toml",
    }
    try:
        with tarfile.open(path, mode="r|gz") as archive:
            for member_count, member in enumerate(archive, start=1):
                if member_count > MAX_ARCHIVE_MEMBERS:
                    raise ArtifactError("sdist has too many members")
                name = member.name.rstrip("/")
                _safe_archive_path(name)
                if name in names:
                    raise ArtifactError(f"duplicate archive path: {name}")
                names.append(name)
                if not (member.isfile() or member.isdir()):
                    raise ArtifactError(f"non-regular sdist member: {member.name}")
                if PurePosixPath(name).parts[0] != expected_stem:
                    raise ArtifactError("sdist members do not share the expected root")
                if member.size > MAX_MEMBER_BYTES:
                    raise ArtifactError(
                        f"sdist member exceeds its limit: {member.name}"
                    )
                total_size += member.size
                if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    raise ArtifactError("sdist exceeds the uncompressed size limit")
                if total_size > max(compressed_size, 1) * MAX_COMPRESSION_RATIO:
                    raise ArtifactError("sdist exceeds the compression ratio limit")
                if name in required_metadata:
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ArtifactError("sdist metadata is not a regular file")
                    metadata_members[name] = _bounded_read(
                        extracted, limit=MAX_METADATA_BYTES, label=name
                    )
    except (OSError, tarfile.TarError, EOFError) as error:
        raise ArtifactError(f"invalid sdist archive: {error}") from error
    return names, metadata_members


def verify_sdist(path: Path, *, expected_name: str, expected_version: str) -> None:
    """Verify source archive paths, structure, metadata, and resource bounds."""
    expected_stem = f"{_distribution_component(expected_name)}-{expected_version}"
    if path.name != f"{expected_stem}.tar.gz":
        raise ArtifactError("sdist filename has an unexpected name or version")
    _require_artifact(path)
    names, metadata_members = _scan_sdist(path, expected_stem=expected_stem)

    required = {
        f"{expected_stem}/PKG-INFO",
        f"{expected_stem}/README.md",
        f"{expected_stem}/pyproject.toml",
        f"{expected_stem}/src/{PUBLIC_IMPORT_PACKAGE}/__init__.py",
    }
    missing = required.difference(names)
    if missing:
        raise ArtifactError(f"sdist is missing required members: {sorted(missing)}")
    if any(
        len(parts := PurePosixPath(name).parts) >= INSTALL_IMPORT_PATH_PARTS
        and parts[:2] == (expected_stem, "src")
        and _is_legacy_import_entry(parts[2])
        for name in names
    ):
        raise ArtifactError("sdist contains the legacy public import package")
    pkg_info_name = f"{expected_stem}/PKG-INFO"
    pyproject_name = f"{expected_stem}/pyproject.toml"
    _require_identity(
        _metadata(metadata_members[pkg_info_name], "sdist PKG-INFO"),
        expected_name=expected_name,
        expected_version=expected_version,
        label="sdist PKG-INFO",
    )
    try:
        project = tomllib.loads(metadata_members[pyproject_name].decode()).get(
            "project", {}
        )
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ArtifactError(f"invalid sdist pyproject.toml: {error}") from error
    if not isinstance(project, dict):
        raise ArtifactError("sdist pyproject.toml has no project table")
    name = project.get("name")
    version = project.get("version")
    if (
        not isinstance(name, str)
        or normalized_name(name) != normalized_name(expected_name)
        or version != expected_version
    ):
        raise ArtifactError("sdist pyproject.toml identifies another release")


def verify_release(
    directory: Path,
    *,
    expected_name: str,
    expected_version: str,
    manifest_name: str = MANIFEST_NAME,
) -> ArtifactSet:
    """Verify the manifest first, then both complete release archives."""
    safe_name = _safe_manifest_name(manifest_name)
    artifacts = discover_artifacts(directory)
    integrity = _verify_manifest(
        directory / safe_name,
        artifacts,
        expected_name=expected_name,
        expected_version=expected_version,
    )
    verified = replace(artifacts, integrity=integrity)
    verify_wheel(
        verified.wheel,
        expected_name=expected_name,
        expected_version=expected_version,
    )
    verify_sdist(
        verified.sdist,
        expected_name=expected_name,
        expected_version=expected_version,
    )
    for artifact in verified.files:
        if file_sha256(artifact) != verified.sha256_for(artifact):
            raise ArtifactError(f"artifact changed during validation: {artifact.name}")
    return verified


def main(argv: list[str] | None = None) -> int:
    """Run release integrity manifest creation or verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create-manifest", "verify"))
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest-name", default=MANIFEST_NAME)
    args = parser.parse_args(argv)
    try:
        if args.action == "create-manifest":
            create_manifest(
                args.directory,
                expected_name=args.name,
                expected_version=args.version,
                manifest_name=args.manifest_name,
            )
        else:
            verify_release(
                args.directory,
                expected_name=args.name,
                expected_version=args.version,
                manifest_name=args.manifest_name,
            )
    except ArtifactError as error:
        parser.exit(1, f"error: {error}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CI
    raise SystemExit(main())
