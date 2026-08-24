"""Validated manifest and deterministic builders for the T04 reference corpus."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import stat
import unicodedata
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from tests.golden.limits import ArchiveLimits
from tests.golden.openxml import WORD_NAMESPACE

CORPUS_CATEGORIES = frozenset(
    {
        "unicode",
        "headings",
        "tables",
        "footnotes",
        "code",
        "local_images",
        "malformed_resources",
        "mermaid",
        "fonts",
        "multiple_templates",
        "malicious_zip",
        "malicious_svg",
        "timeouts",
        "concurrency",
    }
)
FUTURE_OWNERS = frozenset({"T07", "T08", "T09", "T10", "T11", "T13", "T18"})


class CorpusManifestError(ValueError):
    """Raised when the checked-in reference corpus is incomplete or ambiguous."""


@dataclass(frozen=True)
class CorpusCase:
    """One manifest-defined input or generated fixture."""

    case_id: str
    categories: tuple[str, ...]
    kind: str
    purpose: str
    future_owner: str
    entrypoint: PurePosixPath
    files: tuple[PurePosixPath, ...]
    builder: str | None
    expected: Mapping[str, object]
    provenance: Mapping[str, object]


@dataclass(frozen=True)
class CorpusManifest:
    """The validated manifest and its immutable corpus root."""

    version: int
    root: Path
    cases: tuple[CorpusCase, ...]

    def by_id(self, case_id: str) -> CorpusCase:
        for case in self.cases:
            if case.case_id == case_id:
                return case
        raise KeyError(case_id)


def safe_corpus_path(value: object, field: str) -> PurePosixPath:
    """Validate an untrusted manifest/archive path without touching the filesystem."""

    if not isinstance(value, str) or not value or "\0" in value:
        raise CorpusManifestError(f"{field} must be a non-empty path without NUL")
    if "\\" in value or value.startswith("//"):
        raise CorpusManifestError(f"{field} must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.parts in {(), (".",)} or ".." in path.parts:
        raise CorpusManifestError(f"{field} must be a safe relative POSIX path")
    if ":" in path.parts[0]:
        raise CorpusManifestError(f"{field} must not contain a drive prefix")
    if value != path.as_posix():
        raise CorpusManifestError(f"{field} must use a normalized POSIX spelling")
    return path


def _path_key(path: PurePosixPath) -> str:
    return unicodedata.normalize("NFC", path.as_posix()).casefold()


def _required_text(raw: Mapping[str, object], field: str, case_id: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CorpusManifestError(f"Case {case_id} must declare {field}")
    return value


def _parse_case(raw: object) -> CorpusCase:  # noqa: PLR0912 - schema checks stay explicit
    if not isinstance(raw, dict):
        raise CorpusManifestError("Each corpus case must be an object")
    case_id = raw.get("id")
    if (
        not isinstance(case_id, str)
        or not case_id
        or not case_id.replace("-", "").isalnum()
    ):
        raise CorpusManifestError(
            "Corpus case id must contain letters, digits, or hyphens"
        )
    categories_raw = raw.get("categories")
    if (
        not isinstance(categories_raw, list)
        or not categories_raw
        or not all(isinstance(category, str) for category in categories_raw)
    ):
        raise CorpusManifestError(f"Case {case_id} must declare string categories")
    categories = tuple(categories_raw)
    unknown = sorted(set(categories) - CORPUS_CATEGORIES)
    if unknown:
        raise CorpusManifestError(
            f"Case {case_id} has unknown categories: {', '.join(unknown)}"
        )
    if tuple(sorted(set(categories))) != categories:
        raise CorpusManifestError(
            f"Case {case_id} categories must be unique and sorted"
        )
    kind = raw.get("kind")
    if kind not in {"file", "generated_docx", "generated_zip", "scenario"}:
        raise CorpusManifestError(f"Case {case_id} has an unknown kind")
    owner = _required_text(raw, "future_owner", case_id)
    if owner not in FUTURE_OWNERS:
        raise CorpusManifestError(f"Case {case_id} has an unknown future_owner")
    entrypoint = safe_corpus_path(raw.get("entrypoint"), f"{case_id}.entrypoint")
    files_raw = raw.get("files")
    if not isinstance(files_raw, list) or not files_raw:
        raise CorpusManifestError(f"Case {case_id} must declare files")
    files = tuple(safe_corpus_path(value, f"{case_id}.files") for value in files_raw)
    if tuple(sorted(files, key=str)) != files or len(
        {_path_key(item) for item in files}
    ) != len(files):
        raise CorpusManifestError(
            f"Case {case_id} files must be sorted and collision-free"
        )
    if entrypoint not in files:
        raise CorpusManifestError(f"Case {case_id} entrypoint must be listed in files")
    builder = raw.get("builder")
    if builder is not None and not isinstance(builder, str):
        raise CorpusManifestError(f"Case {case_id} builder must be a string")
    if kind.startswith("generated_") != (builder is not None):
        raise CorpusManifestError(f"Case {case_id} builder does not match its kind")
    if builder is not None and len(files) != 1:
        raise CorpusManifestError(
            f"Generated case {case_id} must declare one output file"
        )
    expected = raw.get("expected")
    provenance = raw.get("provenance")
    if not isinstance(expected, dict) or not expected:
        raise CorpusManifestError(f"Case {case_id} must declare expected observations")
    if not isinstance(provenance, dict) or not all(
        isinstance(provenance.get(field), str) and provenance[field]
        for field in ("generator", "license")
    ):
        raise CorpusManifestError(
            f"Case {case_id} must declare generator and license provenance"
        )
    if builder is not None and provenance["generator"] != (
        f"tests.golden.corpus.BUILDERS[{builder}]"
    ):
        raise CorpusManifestError(
            f"Generated case {case_id} provenance must identify its BUILDERS entry"
        )
    return CorpusCase(
        case_id,
        categories,
        kind,
        _required_text(raw, "purpose", case_id),
        owner,
        entrypoint,
        files,
        builder,
        expected,
        provenance,
    )


def _validated_static_file(root: Path, relative: PurePosixPath, case_id: str) -> None:
    candidate = root / relative
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise CorpusManifestError(
                f"Case {case_id} fixture must not be a symlink: {relative}"
            )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CorpusManifestError(
            f"Case {case_id} fixture does not exist: {relative}"
        ) from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise CorpusManifestError(
            f"Case {case_id} fixture is not a regular file: {relative}"
        )


def read_manifest(path: Path) -> CorpusManifest:
    """Read and validate a corpus manifest, including provenance and coverage."""

    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CorpusManifestError("Corpus manifest is not valid UTF-8 JSON") from error
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise CorpusManifestError("Corpus manifest version must be 1")
    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise CorpusManifestError("Corpus manifest must contain cases")
    cases = tuple(_parse_case(item) for item in raw_cases)
    case_ids = tuple(case.case_id for case in cases)
    if case_ids != tuple(sorted(case_ids)) or len(case_ids) != len(set(case_ids)):
        raise CorpusManifestError("Corpus case ids must be unique and sorted")
    missing = sorted(
        CORPUS_CATEGORIES - {category for case in cases for category in case.categories}
    )
    if missing:
        raise CorpusManifestError(
            f"Corpus categories are missing: {', '.join(missing)}"
        )
    root = path.parent.resolve()
    static_keys: dict[str, PurePosixPath] = {}
    for case in cases:
        for relative in case.files:
            key = _path_key(relative)
            if case.builder is None and key in static_keys:
                raise CorpusManifestError(
                    f"Normalized corpus path collision: {relative}"
                )
            static_keys[key] = relative
            if case.builder is None:
                _validated_static_file(root, relative, case.case_id)
        if case.builder is not None:
            payload = build_case_bytes(case)
            if case.provenance.get("sha256") != hashlib.sha256(payload).hexdigest():
                raise CorpusManifestError(
                    f"Generated case {case.case_id} has a stale sha256"
                )
            if case.provenance.get("size_bytes") != len(payload):
                raise CorpusManifestError(
                    f"Generated case {case.case_id} has a stale size_bytes"
                )
    return CorpusManifest(version=1, root=root, cases=cases)


def _zip_bytes(entries: tuple[tuple[zipfile.ZipInfo, bytes], ...]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for info, payload in entries:
            info.date_time = (1980, 1, 1, 0, 0, 0)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            if not info.external_attr:
                info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, payload)
    return output.getvalue()


def _docx_builder(case: CorpusCase) -> bytes:
    style_id, label = str(case.expected["style_id"]), str(case.expected["label"])
    content_types = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>"""
    root_relationships = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>"""
    document = f'''<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{WORD_NAMESPACE}"><w:body><w:p><w:pPr><w:pStyle w:val="{style_id}"/></w:pPr><w:r><w:t>{label}</w:t></w:r></w:p><w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:body></w:document>'''.encode()
    styles = f'''<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="{WORD_NAMESPACE}"><w:style w:type="paragraph" w:styleId="{style_id}"><w:name w:val="{label}"/></w:style></w:styles>'''.encode()
    return _zip_bytes(
        tuple(
            (zipfile.ZipInfo(name), payload)
            for name, payload in (
                ("[Content_Types].xml", content_types),
                ("_rels/.rels", root_relationships),
                ("word/document.xml", document),
                ("word/styles.xml", styles),
            )
        )
    )


def _path_traversal_zip(_: CorpusCase) -> bytes:
    return _zip_bytes(
        (
            (zipfile.ZipInfo("document.md"), b"# Unsafe archive\n"),
            (zipfile.ZipInfo("../escape.txt"), b"must not be extracted\n"),
        )
    )


def _symlink_zip(_: CorpusCase) -> bytes:
    link = zipfile.ZipInfo("assets/link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    return _zip_bytes(
        (
            (zipfile.ZipInfo("document.md"), b"# Symlink archive\n"),
            (link, b"../../outside"),
        )
    )


BUILDERS: Mapping[str, Callable[[CorpusCase], bytes]] = {
    "minimal_reference_docx": _docx_builder,
    "path_traversal_zip": _path_traversal_zip,
    "symlink_zip": _symlink_zip,
}


def build_case_bytes(case: CorpusCase) -> bytes:
    """Build a generated case deterministically without writing it to disk."""

    if case.builder is None:
        raise CorpusManifestError(f"Case {case.case_id} is not generated")
    try:
        return BUILDERS[case.builder](case)
    except KeyError as error:
        raise CorpusManifestError(
            f"Case {case.case_id} uses an unknown builder: {case.builder}"
        ) from error


def inspect_archive_fixture(data: bytes, limits: ArchiveLimits) -> tuple[str, ...]:
    """Validate ZIP metadata without extraction; caps apply only to this test helper."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise CorpusManifestError("Fixture is not a valid ZIP archive") from error
    with archive:
        members = archive.infolist()
        if len(members) > limits.max_entries:
            raise CorpusManifestError("Fixture exceeds the archive entry cap")
        names: dict[str, str] = {}
        total = 0
        for member in members:
            path = safe_corpus_path(
                member.filename.removesuffix("/")
                if member.is_dir()
                else member.filename,
                "archive member",
            )
            key = _path_key(path)
            if key in names:
                raise CorpusManifestError(
                    "Fixture contains duplicate normalized member names"
                )
            names[key] = member.filename
            if member.flag_bits & 1:
                raise CorpusManifestError("Fixture contains an encrypted member")
            if stat.S_ISLNK(member.external_attr >> 16):
                raise CorpusManifestError("Fixture contains a symbolic-link member")
            if member.file_size > limits.max_member_uncompressed_bytes:
                raise CorpusManifestError(
                    "Fixture member exceeds the uncompressed-size cap"
                )
            compressed_size = max(member.compress_size, 1)
            if member.file_size / compressed_size > limits.max_compression_ratio:
                raise CorpusManifestError("Fixture exceeds the compression-ratio cap")
            total += member.file_size
            if total > limits.max_total_uncompressed_bytes:
                raise CorpusManifestError(
                    "Fixture exceeds the total uncompressed-size cap"
                )
        return tuple(sorted(names.values()))


def materialize_case(
    manifest: CorpusManifest, case: CorpusCase, destination: Path
) -> Path:
    """Materialize one case under an isolated directory and return its entrypoint."""

    case_root = destination / case.case_id
    case_root.mkdir(parents=True, exist_ok=False)
    if case.builder is not None:
        output = case_root / case.entrypoint
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(build_case_bytes(case))
        return output
    common = case.entrypoint.parent
    for relative in case.files:
        try:
            output_relative = relative.relative_to(common)
        except ValueError as error:
            raise CorpusManifestError(
                f"Case {case.case_id} files must share the entrypoint directory"
            ) from error
        output = case_root / output_relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(manifest.root / relative, output)
    return case_root / case.entrypoint.name
