"""Create and verify deterministic integrity metadata for Python artifacts."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import re
import stat
import tarfile
import tomllib
import zipfile
from dataclasses import dataclass
from email.message import Message
from email.parser import BytesParser
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

MANIFEST_NAME = "release-integrity.json"
MANIFEST_SCHEMA_VERSION = 1
MAX_METADATA_BYTES = 1_048_576
RECORD = "RECORD"
ARTIFACT_COUNT = 2
RECORD_FIELD_COUNT = 3


class ArtifactError(ValueError):
    """An artifact or its integrity manifest is invalid."""


@dataclass(frozen=True)
class ArtifactSet:
    """The single wheel and source distribution in a release directory."""

    wheel: Path
    sdist: Path

    @property
    def files(self) -> tuple[Path, Path]:
        """Return artifacts in deterministic filename order."""
        first, second = sorted((self.wheel, self.sdist))
        return first, second


def normalized_name(value: str) -> str:
    """Return the normalized Python distribution name."""
    return re.sub(r"[-_.]+", "-", value).lower()


def _distribution_component(value: str) -> str:
    return normalized_name(value).replace("-", "_")


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
        _safe_archive_path(value.rstrip("/"))
        key = value.rstrip("/")
        if key in seen:
            raise ArtifactError(f"duplicate archive path: {key}")
        seen.add(key)
        result.append(key)
    return result


def discover_artifacts(directory: Path) -> ArtifactSet:
    """Discover exactly one wheel and one source distribution."""
    if not directory.is_dir():
        raise ArtifactError(f"artifact directory does not exist: {directory}")
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1:
        raise ArtifactError(f"expected exactly one wheel, found {len(wheels)}")
    if len(sdists) != 1:
        raise ArtifactError(f"expected exactly one sdist, found {len(sdists)}")
    return ArtifactSet(wheel=wheels[0], sdist=sdists[0])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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
    artifacts = discover_artifacts(directory)
    document = {
        "algorithm": "sha256",
        "artifacts": [
            {
                "filename": path.name,
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            }
            for path in artifacts.files
        ],
        "name": expected_name,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "version": expected_version,
    }
    target = directory / manifest_name
    target.write_bytes(_canonical_json(document))
    return target


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        document: Any = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
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
) -> None:
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
    actual_names = [path.name for path in artifacts.files]
    if [
        entry.get("filename") for entry in entries if isinstance(entry, dict)
    ] != actual_names:
        raise ArtifactError(
            "integrity manifest artifact list does not match the directory"
        )
    for entry, artifact in zip(entries, artifacts.files, strict=True):
        if not isinstance(entry, dict) or set(entry) != {"filename", "sha256", "size"}:
            raise ArtifactError("invalid integrity manifest artifact entry")
        if entry["size"] != artifact.stat().st_size or entry["sha256"] != _sha256(
            artifact
        ):
            raise ArtifactError(f"integrity check failed for {artifact.name}")


def _metadata(data: bytes, label: str) -> Message:
    if len(data) > MAX_METADATA_BYTES:
        raise ArtifactError(f"{label} exceeds the metadata size limit")
    try:
        message = BytesParser().parsebytes(data)
    except Exception as error:  # email parser exposes no narrower exception contract
        raise ArtifactError(f"invalid {label}: {error}") from error
    return message


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


def _zip_member_bytes(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_METADATA_BYTES:
        raise ArtifactError(f"{name} exceeds the metadata size limit")
    return archive.read(info)


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
    for row in rows:
        member_name, encoded_hash, encoded_size = row
        if member_name == record_name:
            if encoded_hash or encoded_size:
                raise ArtifactError("wheel RECORD must not hash itself")
            continue
        info = archive.getinfo(member_name)
        expected_hash = (
            base64.urlsafe_b64encode(hashlib.sha256(archive.read(info)).digest())
            .rstrip(b"=")
            .decode()
        )
        if encoded_hash != f"sha256={expected_hash}" or encoded_size != str(
            info.file_size
        ):
            raise ArtifactError(f"wheel RECORD integrity failed for {member_name}")


def verify_wheel(path: Path, *, expected_name: str, expected_version: str) -> None:
    """Verify the wheel filename, metadata, contents, and RECORD integrity."""
    _wheel_identity(path, expected_name, expected_version)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
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
            required = {
                f"{_distribution_component(expected_name)}/__init__.py",
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
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise ArtifactError(f"invalid wheel archive: {error}") from error


def verify_sdist(path: Path, *, expected_name: str, expected_version: str) -> None:
    """Verify the source archive paths, structure, and package metadata."""
    expected_stem = f"{_distribution_component(expected_name)}-{expected_version}"
    if path.name != f"{expected_stem}.tar.gz":
        raise ArtifactError("sdist filename has an unexpected name or version")
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            names = _require_unique_paths(member.name for member in members)
            for member in members:
                if not (member.isfile() or member.isdir()):
                    raise ArtifactError(f"non-regular sdist member: {member.name}")
                if PurePosixPath(member.name).parts[0] != expected_stem:
                    raise ArtifactError("sdist members do not share the expected root")
            required = {
                f"{expected_stem}/PKG-INFO",
                f"{expected_stem}/README.md",
                f"{expected_stem}/pyproject.toml",
                f"{expected_stem}/src/{_distribution_component(expected_name)}/__init__.py",
            }
            missing = required.difference(names)
            if missing:
                raise ArtifactError(
                    f"sdist is missing required members: {sorted(missing)}"
                )
            pkg_info = archive.extractfile(f"{expected_stem}/PKG-INFO")
            pyproject = archive.extractfile(f"{expected_stem}/pyproject.toml")
            if pkg_info is None or pyproject is None:
                raise ArtifactError("sdist metadata members are not regular files")
            _require_identity(
                _metadata(pkg_info.read(MAX_METADATA_BYTES + 1), "sdist PKG-INFO"),
                expected_name=expected_name,
                expected_version=expected_version,
                label="sdist PKG-INFO",
            )
            raw_pyproject = pyproject.read(MAX_METADATA_BYTES + 1)
            if len(raw_pyproject) > MAX_METADATA_BYTES:
                raise ArtifactError(
                    "sdist pyproject.toml exceeds the metadata size limit"
                )
            project = tomllib.loads(raw_pyproject.decode()).get("project", {})
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
    except (
        OSError,
        tarfile.TarError,
        KeyError,
        UnicodeDecodeError,
        tomllib.TOMLDecodeError,
    ) as error:
        raise ArtifactError(f"invalid sdist archive: {error}") from error


def verify_release(
    directory: Path,
    *,
    expected_name: str,
    expected_version: str,
    manifest_name: str = MANIFEST_NAME,
) -> ArtifactSet:
    """Verify a complete release artifact directory."""
    artifacts = discover_artifacts(directory)
    verify_wheel(
        artifacts.wheel,
        expected_name=expected_name,
        expected_version=expected_version,
    )
    verify_sdist(
        artifacts.sdist,
        expected_name=expected_name,
        expected_version=expected_version,
    )
    _verify_manifest(
        directory / manifest_name,
        artifacts,
        expected_name=expected_name,
        expected_version=expected_version,
    )
    return artifacts


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
