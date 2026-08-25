"""Tests for Python package artifact verification."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from scripts.release import artifacts as artifact_module
from scripts.release.artifacts import (
    MAX_MANIFEST_BYTES,
    ArtifactError,
    create_manifest,
    discover_artifacts,
    main,
    verify_release,
    verify_sdist,
    verify_wheel,
)

pytestmark = pytest.mark.unit

NAME = "md-converter"
VERSION = "0.1.0"
DIST = "md_converter"
ROOT = f"{DIST}-{VERSION}"
DIST_INFO = f"{ROOT}.dist-info"


def _record(contents: dict[str, bytes]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    for name, data in contents.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        writer.writerow((name, f"sha256={digest.decode()}", len(data)))
    writer.writerow((f"{DIST_INFO}/RECORD", "", ""))
    return stream.getvalue().encode()


def _write_wheel(
    directory: Path,
    *,
    extra: tuple[str, bytes] | None = None,
    metadata_identity: tuple[str, str] = (NAME, VERSION),
    bad_record: bool = False,
    wheel_tag: str = "py3-none-any",
) -> Path:
    metadata_name, metadata_version = metadata_identity
    path = directory / f"{ROOT}-py3-none-any.whl"
    contents = {
        f"{DIST}/__init__.py": b'__version__ = "0.1.0"\n',
        f"{DIST_INFO}/METADATA": (
            f"Metadata-Version: 2.4\nName: {metadata_name}\n"
            f"Version: {metadata_version}\n\n"
        ).encode(),
        f"{DIST_INFO}/WHEEL": (
            f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {wheel_tag}\n\n"
        ).encode(),
    }
    if extra is not None:
        contents[extra[0]] = extra[1]
    record = _record(contents)
    if bad_record:
        record = record.replace(b"sha256=", b"sha256=broken", 1)
    contents[f"{DIST_INFO}/RECORD"] = record
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return path


def _write_sdist(
    directory: Path,
    *,
    extra_name: str | None = None,
    link_name: str | None = None,
    metadata_version: str = VERSION,
    project_version: str = VERSION,
) -> Path:
    path = directory / f"{ROOT}.tar.gz"
    contents = {
        f"{ROOT}/PKG-INFO": (
            f"Metadata-Version: 2.4\nName: {NAME}\nVersion: {metadata_version}\n\n"
        ).encode(),
        f"{ROOT}/README.md": b"# Package\n",
        f"{ROOT}/pyproject.toml": (
            f'[project]\nname = "{NAME}"\nversion = "{project_version}"\n'
        ).encode(),
        f"{ROOT}/src/{DIST}/__init__.py": b'__version__ = "0.1.0"\n',
    }
    if extra_name is not None:
        contents[extra_name] = b"unexpected"
    with tarfile.open(path, mode="w:gz") as archive:
        for name, data in contents.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(data))
        if link_name is not None:
            info = tarfile.TarInfo(link_name)
            info.type = tarfile.SYMTYPE
            info.linkname = f"{ROOT}/README.md"
            archive.addfile(info)
    return path


@pytest.fixture
def release_directory(tmp_path: Path) -> Path:
    """Create a valid wheel, sdist, and integrity manifest."""
    _write_wheel(tmp_path)
    _write_sdist(tmp_path)
    create_manifest(tmp_path, expected_name=NAME, expected_version=VERSION)
    return tmp_path


def test_verify_release_accepts_complete_artifact_set(release_directory: Path) -> None:
    """A matching wheel, sdist, and canonical manifest pass verification."""
    artifacts = verify_release(
        release_directory, expected_name=NAME, expected_version=VERSION
    )
    assert artifacts.wheel.name == f"{ROOT}-py3-none-any.whl"
    assert artifacts.sdist.name == f"{ROOT}.tar.gz"
    assert (
        artifacts.sha256_for(artifacts.wheel)
        == hashlib.sha256(artifacts.wheel.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("suffix", [".whl", ".tar.gz"])
def test_discovery_rejects_duplicate_distribution_artifacts(
    release_directory: Path, suffix: str
) -> None:
    """The release directory cannot ambiguously contain two artifact candidates."""
    (release_directory / f"another{suffix}").write_bytes(b"duplicate")
    with pytest.raises(ArtifactError, match="exactly one"):
        discover_artifacts(release_directory)


def test_discovery_rejects_symlink_artifact(tmp_path: Path) -> None:
    """Release artifacts themselves cannot be redirected through symlinks."""
    real_wheel = tmp_path / "real-wheel"
    real_wheel.write_bytes(b"wheel")
    (tmp_path / f"{ROOT}-py3-none-any.whl").symlink_to(real_wheel)
    _write_sdist(tmp_path)
    with pytest.raises(ArtifactError, match="cannot open"):
        discover_artifacts(tmp_path)


def test_verify_release_rejects_changed_artifact(release_directory: Path) -> None:
    """The external manifest detects bytes changed after it was created."""
    with (release_directory / f"{ROOT}.tar.gz").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ArtifactError, match="integrity check failed"):
        verify_release(release_directory, expected_name=NAME, expected_version=VERSION)


def test_verify_release_rehashes_after_archive_inspection(
    release_directory: Path, mocker: MockerFixture
) -> None:
    """Mutation during inner validation cannot retain a stale manifest binding."""

    def mutate_during_inspection(path: Path, **kwargs: str) -> None:
        with path.open("ab") as stream:
            stream.write(b"changed during inspection")

    mocker.patch.object(
        artifact_module, "verify_wheel", side_effect=mutate_during_inspection
    )
    with pytest.raises(ArtifactError, match="changed during validation"):
        verify_release(release_directory, expected_name=NAME, expected_version=VERSION)


def test_verify_release_rejects_noncanonical_manifest(release_directory: Path) -> None:
    """Manifest formatting is deterministic and independently enforceable."""
    manifest = release_directory / "release-integrity.json"
    document = json.loads(manifest.read_text())
    manifest.write_text(json.dumps(document, indent=2))
    with pytest.raises(ArtifactError, match="not canonical"):
        verify_release(release_directory, expected_name=NAME, expected_version=VERSION)


@pytest.mark.parametrize(
    "manifest_name",
    [
        "../manifest.json",
        "/var/manifest.json",
        "subdir/manifest.json",
        r"C:\manifest",
    ],
)
def test_manifest_name_must_be_a_safe_basename(
    release_directory: Path, manifest_name: str
) -> None:
    """Callers cannot redirect manifest reads or writes outside the artifact set."""
    with pytest.raises(ArtifactError, match="unsafe integrity manifest name"):
        verify_release(
            release_directory,
            expected_name=NAME,
            expected_version=VERSION,
            manifest_name=manifest_name,
        )


def test_verify_release_rejects_symlink_manifest(
    release_directory: Path, tmp_path: Path
) -> None:
    """A manifest cannot be substituted through a symbolic link."""
    manifest = release_directory / "release-integrity.json"
    copy = tmp_path / "manifest-copy.json"
    copy.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(copy)
    with pytest.raises(ArtifactError, match="cannot open integrity manifest"):
        verify_release(release_directory, expected_name=NAME, expected_version=VERSION)


def test_verify_release_rejects_oversized_manifest(release_directory: Path) -> None:
    """Manifest loading is bounded before JSON parsing."""
    manifest = release_directory / "release-integrity.json"
    manifest.write_bytes(b"{" + b" " * MAX_MANIFEST_BYTES + b"}")
    with pytest.raises(ArtifactError, match="exceeds the size limit"):
        verify_release(release_directory, expected_name=NAME, expected_version=VERSION)


@pytest.mark.parametrize(
    ("metadata_name", "metadata_version", "message"),
    [("another", VERSION, "Name"), (NAME, "9.9.9", "Version")],
)
def test_wheel_rejects_wrong_metadata_identity(
    tmp_path: Path, metadata_name: str, metadata_version: str, message: str
) -> None:
    """Wheel metadata must identify the expected release."""
    wheel = _write_wheel(
        tmp_path,
        metadata_identity=(metadata_name, metadata_version),
    )
    with pytest.raises(ArtifactError, match=message):
        verify_wheel(wheel, expected_name=NAME, expected_version=VERSION)


def test_wheel_rejects_path_traversal(tmp_path: Path) -> None:
    """Wheel members cannot escape their archive root."""
    wheel = _write_wheel(tmp_path, extra=("../outside", b"unsafe"))
    with pytest.raises(ArtifactError, match="unsafe archive path"):
        verify_wheel(wheel, expected_name=NAME, expected_version=VERSION)


def test_wheel_rejects_invalid_record_hash(tmp_path: Path) -> None:
    """Every non-RECORD wheel member needs a matching SHA-256 and size."""
    wheel = _write_wheel(tmp_path, bad_record=True)
    with pytest.raises(ArtifactError, match="RECORD integrity failed"):
        verify_wheel(wheel, expected_name=NAME, expected_version=VERSION)


def test_wheel_rejects_metadata_tag_mismatching_filename(tmp_path: Path) -> None:
    """The WHEEL tag must include the compatibility tag in the filename."""
    wheel = _write_wheel(tmp_path, wheel_tag="cp314-cp314-linux_x86_64")
    with pytest.raises(ArtifactError, match="WHEEL metadata is incomplete"):
        verify_wheel(wheel, expected_name=NAME, expected_version=VERSION)


def test_wheel_rejects_generated_bytecode(tmp_path: Path) -> None:
    """Generated bytecode is not a permitted wheel payload."""
    wheel = _write_wheel(
        tmp_path, extra=(f"{DIST}/__pycache__/module.pyc", b"bytecode")
    )
    with pytest.raises(ArtifactError, match="bytecode"):
        verify_wheel(wheel, expected_name=NAME, expected_version=VERSION)


def test_wheel_rejects_member_count_bomb(tmp_path: Path, mocker: MockerFixture) -> None:
    """A ZIP central directory cannot force unbounded member processing."""
    wheel = _write_wheel(tmp_path)
    mocker.patch.object(artifact_module, "MAX_ARCHIVE_MEMBERS", 3)
    with pytest.raises(ArtifactError, match="too many members"):
        verify_wheel(wheel, expected_name=NAME, expected_version=VERSION)


def test_wheel_rejects_compression_bomb(tmp_path: Path, mocker: MockerFixture) -> None:
    """Highly compressed aggregate payloads are rejected before extraction."""
    wheel = _write_wheel(tmp_path, extra=(f"{DIST}/zeros.bin", b"\0" * 4096))
    mocker.patch.object(artifact_module, "MAX_COMPRESSION_RATIO", 1)
    with pytest.raises(ArtifactError, match="compression ratio"):
        verify_wheel(wheel, expected_name=NAME, expected_version=VERSION)


def test_sdist_rejects_path_traversal(tmp_path: Path) -> None:
    """Source distribution members cannot escape their archive root."""
    sdist = _write_sdist(tmp_path, extra_name=f"{ROOT}/../outside")
    with pytest.raises(ArtifactError, match="unsafe archive path"):
        verify_sdist(sdist, expected_name=NAME, expected_version=VERSION)


def test_sdist_rejects_links(tmp_path: Path) -> None:
    """Links cannot redirect source distribution extraction."""
    sdist = _write_sdist(tmp_path, link_name=f"{ROOT}/linked-readme")
    with pytest.raises(ArtifactError, match="non-regular"):
        verify_sdist(sdist, expected_name=NAME, expected_version=VERSION)


def test_sdist_rejects_member_count_bomb(tmp_path: Path, mocker: MockerFixture) -> None:
    """Streaming tar inspection stops at the configured member bound."""
    sdist = _write_sdist(tmp_path)
    mocker.patch.object(artifact_module, "MAX_ARCHIVE_MEMBERS", 3)
    with pytest.raises(ArtifactError, match="too many members"):
        verify_sdist(sdist, expected_name=NAME, expected_version=VERSION)


def test_sdist_rejects_declared_member_size_bomb(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    """Tar member sizes are bounded before their payload is consumed."""
    sdist = _write_sdist(tmp_path)
    mocker.patch.object(artifact_module, "MAX_MEMBER_BYTES", 8)
    with pytest.raises(ArtifactError, match="member exceeds its limit"):
        verify_sdist(sdist, expected_name=NAME, expected_version=VERSION)


@pytest.mark.parametrize(
    ("metadata_version", "project_version", "message"),
    [("9.9.9", VERSION, "PKG-INFO"), (VERSION, "9.9.9", "pyproject")],
)
def test_sdist_rejects_wrong_metadata_identity(
    tmp_path: Path, metadata_version: str, project_version: str, message: str
) -> None:
    """Both sdist metadata sources must identify the expected release."""
    sdist = _write_sdist(
        tmp_path,
        metadata_version=metadata_version,
        project_version=project_version,
    )
    with pytest.raises(ArtifactError, match=message):
        verify_sdist(sdist, expected_name=NAME, expected_version=VERSION)


def test_cli_reports_validation_errors(tmp_path: Path) -> None:
    """The command-line verifier exits nonzero with an actionable error."""
    with pytest.raises(SystemExit) as raised:
        main(
            [
                "verify",
                "--directory",
                str(tmp_path),
                "--name",
                NAME,
                "--version",
                VERSION,
            ]
        )
    assert raised.value.code == 1
