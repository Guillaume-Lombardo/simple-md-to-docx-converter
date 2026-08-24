"""Isolated, bounded DOCX-to-PDF conversion through LibreOffice."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import signal
import stat
import subprocess
import tempfile
import threading
import time
import unicodedata
import zipfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import NoReturn

from pypdf import PdfReader
from pypdf import filters as pypdf_filters
from pypdf.errors import LimitReachedError, PyPdfError
from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject

from md_converter.conversion.errors import ConversionError, ConversionErrorCode

_PDF_EXPORT_FILTER = "pdf:writer_pdf_Export"
_OUTPUT_FORMAT = "pdf"
_READ_CHUNK_BYTES = 64 * 1024
_PROHIBITED_PDF_KEYS = frozenset(
    {
        "/AA",
        "/AcroForm",
        "/AF",
        "/EF",
        "/EmbeddedFiles",
        "/Filespec",
        "/JS",
        "/JavaScript",
        "/OpenAction",
        "/RichMedia",
        "/XFA",
    }
)
_PROHIBITED_ACTIONS = frozenset(
    {
        "/GoTo3DView",
        "/GoToE",
        "/GoToR",
        "/Hide",
        "/ImportData",
        "/JavaScript",
        "/Launch",
        "/Movie",
        "/Named",
        "/Rendition",
        "/ResetForm",
        "/RichMediaExecute",
        "/SetOCGState",
        "/Sound",
        "/SubmitForm",
        "/Thread",
        "/Trans",
    }
)
_SAFE_PDF_ACTIONS = frozenset({"/GoTo", "/URI"})
_HEX_DIGITS = frozenset("0123456789abcdef")
_MAX_METADATA_CHARACTERS = 256
_SHA256_CHARACTERS = 64
_PDF_FILTER_LIMIT_NAMES = (
    "FLATE_MAX_BUFFER_SIZE",
    "JBIG2_MAX_OUTPUT_LENGTH",
    "LZW_MAX_OUTPUT_LENGTH",
    "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH",
    "MAX_DECLARED_STREAM_LENGTH",
    "RUN_LENGTH_MAX_OUTPUT_LENGTH",
    "ZLIB_MAX_OUTPUT_LENGTH",
    "ZLIB_MAX_RECOVERY_INPUT_LENGTH",
)
_PDF_PARSE_LOCK = threading.Lock()
_REQUIRED_DOCX_PARTS = frozenset(
    {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
)
_SUPPORTED_DOCX_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


def _safe_metadata(value: str) -> bool:
    return (
        type(value) is str
        and 0 < len(value) <= _MAX_METADATA_CHARACTERS
        and value == value.strip()
        and not any(unicodedata.category(char).startswith("C") for char in value)
    )


def _is_sha256(value: str) -> bool:
    return len(value) == _SHA256_CHARACTERS and set(value) <= _HEX_DIGITS


@dataclass(frozen=True, slots=True)
class PdfLimits:
    """Explicit T18-owned byte and structural bounds."""

    max_docx_bytes: int
    max_docx_entries: int
    max_docx_member_uncompressed_bytes: int
    max_docx_total_uncompressed_bytes: int
    max_docx_compression_ratio: float
    max_pdf_bytes: int
    max_pdf_decoded_stream_bytes: int
    max_pages: int
    max_pdf_objects: int
    max_pdf_object_depth: int

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (
                self.max_docx_bytes,
                self.max_docx_entries,
                self.max_docx_member_uncompressed_bytes,
                self.max_docx_total_uncompressed_bytes,
                self.max_pdf_bytes,
                self.max_pdf_decoded_stream_bytes,
                self.max_pages,
                self.max_pdf_objects,
                self.max_pdf_object_depth,
            )
        ):
            raise ValueError("PDF limits must be positive integers")
        if (
            type(self.max_docx_compression_ratio) not in {int, float}
            or not math.isfinite(self.max_docx_compression_ratio)
            or self.max_docx_compression_ratio < 1.0
        ):
            raise ValueError(
                "DOCX compression ratio must be a finite number at least 1"
            )


@dataclass(frozen=True, slots=True)
class LibreOfficeConfig:
    """Explicit engine identity, deadlines, polling, and workspace root."""

    executable: str
    version: str
    timeout_seconds: float
    termination_grace_seconds: float
    cancellation_poll_seconds: float
    workspace_root: Path | None = None

    def __post_init__(self) -> None:
        if not _safe_metadata(self.executable) or "\0" in self.executable:
            raise ValueError(
                "LibreOffice executable must be a non-empty path or command"
            )
        if not _safe_metadata(self.version):
            raise ValueError("LibreOffice version must be safe non-empty metadata")
        for value in (
            self.timeout_seconds,
            self.termination_grace_seconds,
            self.cancellation_poll_seconds,
        ):
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("LibreOffice deadlines must be finite and positive")
        if self.workspace_root is not None and not self.workspace_root.is_dir():
            raise ValueError("LibreOffice workspace root must be an existing directory")


@dataclass(frozen=True, slots=True)
class PdfTraceabilityContext:
    """Caller-owned immutable versions needed to reproduce one conversion."""

    application_version: str
    conversion_contract_version: str
    template_id: str
    template_version: str
    template_sha256: str
    pandoc_version: str
    pandoc_reader: str
    mermaid_version: str
    chromium_version: str
    font_manifest_sha256: str

    def __post_init__(self) -> None:
        metadata = (
            self.application_version,
            self.conversion_contract_version,
            self.template_id,
            self.template_version,
            self.pandoc_version,
            self.pandoc_reader,
            self.mermaid_version,
            self.chromium_version,
        )
        if any(not _safe_metadata(value) for value in metadata):
            raise ValueError("PDF traceability metadata must be safe and non-empty")
        if not _is_sha256(self.template_sha256) or not _is_sha256(
            self.font_manifest_sha256
        ):
            raise ValueError("PDF traceability digests must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class PdfPage:
    """One validated PDF page size in points."""

    width_points: float
    height_points: float


@dataclass(frozen=True, slots=True)
class PdfTraceabilityManifest:
    """Canonical, content-free reproduction metadata external to PDF bytes."""

    schema_version: int
    application_version: str
    conversion_contract_version: str
    template_id: str
    template_version: str
    template_sha256: str
    source_docx_sha256: str
    output_pdf_sha256: str
    output_pdf_bytes: int
    pages: tuple[PdfPage, ...]
    pandoc_version: str
    pandoc_reader: str
    mermaid_version: str
    chromium_version: str
    libreoffice_version: str
    font_manifest_sha256: str
    export_filter: str
    output_format: str

    def canonical_json(self) -> bytes:
        """Serialize without timestamps, paths, user data, or unstable whitespace."""

        return json.dumps(
            asdict(self), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class PdfArtifact:
    """Validated PDF bytes paired with their immutable traceability manifest."""

    pdf: bytes
    manifest: PdfTraceabilityManifest


def _error(code: ConversionErrorCode, message: str) -> NoReturn:
    raise ConversionError(code, message)


def _environment(workspace: Path, host: Mapping[str, str]) -> dict[str, str]:
    environment = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
    for name in ("PATH", "FONTCONFIG_FILE", "FONTCONFIG_PATH"):
        if name in host:
            environment[name] = host[name]
    environment.update(
        {
            "HOME": str(workspace / "home"),
            "TMPDIR": str(workspace / "tmp"),
            "XDG_CACHE_HOME": str(workspace / "cache"),
            "XDG_CONFIG_HOME": str(workspace / "config"),
            "XDG_DATA_HOME": str(workspace / "data"),
        }
    )
    return environment


def _safe_docx_member(
    member: zipfile.ZipInfo, names: set[str], limits: PdfLimits
) -> bool:
    name = member.filename
    path = PurePosixPath(name)
    compressed_size = member.compress_size
    ratio = (
        member.file_size / compressed_size
        if compressed_size
        else math.inf
        if member.file_size
        else 0.0
    )
    return not (
        not name
        or "\0" in name
        or "\\" in name
        or path.is_absolute()
        or ".." in path.parts
        or stat.S_ISLNK(member.external_attr >> 16)
        or member.flag_bits & 1
        or member.compress_type not in _SUPPORTED_DOCX_COMPRESSION
        or name in names
        or member.file_size > limits.max_docx_member_uncompressed_bytes
        or (member.file_size and compressed_size <= 0)
        or ratio > limits.max_docx_compression_ratio
    )


def _safe_docx(data: bytes, limits: PdfLimits) -> bool:
    """Preflight the trusted pipeline DOCX without expanding archive members."""

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except OSError, zipfile.BadZipFile:
        return False
    with archive:
        members = archive.infolist()
        if not members or len(members) > limits.max_docx_entries:
            return False
        names: set[str] = set()
        total_uncompressed = 0
        for member in members:
            if not _safe_docx_member(member, names, limits):
                return False
            total_uncompressed += member.file_size
            if total_uncompressed > limits.max_docx_total_uncompressed_bytes:
                return False
            names.add(member.filename)
        return names >= _REQUIRED_DOCX_PARTS


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    return True


def _terminate_group(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace_seconds
    while _process_group_exists(process_group):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if process.poll() is None:
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=remaining)
        else:
            time.sleep(min(0.01, remaining))
    if not _process_group_exists(process_group):
        return
    with suppress(ProcessLookupError):
        os.killpg(process_group, signal.SIGKILL)
    with suppress(subprocess.TimeoutExpired):
        process.wait(timeout=grace_seconds)


def _read_pdf(path: Path, limit: int) -> bytes:
    descriptor = -1
    chunks: list[bytes] = []
    size = 0
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size <= 0:
            raise OSError
        if metadata.st_size > limit:
            _error(
                ConversionErrorCode.PDF_LIMIT_EXCEEDED,
                "PDF output exceeds configured limits.",
            )
        while size <= limit:
            chunk = os.read(descriptor, min(_READ_CHUNK_BYTES, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except ConversionError:
        raise
    except OSError:
        _error(ConversionErrorCode.INVALID_PDF, "LibreOffice produced an invalid PDF.")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if size > limit:
        _error(
            ConversionErrorCode.PDF_LIMIT_EXCEEDED,
            "PDF output exceeds configured limits.",
        )
    if size != metadata.st_size:
        _error(ConversionErrorCode.INVALID_PDF, "LibreOffice produced an invalid PDF.")
    return b"".join(chunks)


@dataclass
class _PdfWalkState:
    limits: PdfLimits
    seen_indirect: set[tuple[int, int]] = field(default_factory=set)
    seen_direct: set[int] = field(default_factory=set)
    count: int = 0

    def claim(self) -> None:
        self.count += 1
        if self.count > self.limits.max_pdf_objects:
            _error(
                ConversionErrorCode.PDF_LIMIT_EXCEEDED,
                "PDF exceeds configured limits.",
            )


def _walk_pdf_dictionary(
    value: DictionaryObject,
    state: _PdfWalkState,
    depth: int,
) -> None:
    action_value = value.get("/S")
    action = (
        action_value.get_object()
        if isinstance(action_value, IndirectObject)
        else action_value
    )
    action_name = str(action) if action is not None else ""
    dictionary_type = str(value.get("/Type", ""))
    if action_name in _PROHIBITED_ACTIONS or (
        dictionary_type == "/Action" and action_name not in _SAFE_PDF_ACTIONS
    ):
        _error(
            ConversionErrorCode.INVALID_PDF,
            "LibreOffice produced an unsafe PDF.",
        )
    for key, child in value.items():
        name = str(key)
        if name in _PROHIBITED_PDF_KEYS:
            _error(
                ConversionErrorCode.INVALID_PDF,
                "LibreOffice produced an unsafe PDF.",
            )
        _walk_pdf_object(child, state, depth + 1)


def _walk_pdf_object(
    value: object,
    state: _PdfWalkState,
    depth: int,
) -> None:
    if depth > state.limits.max_pdf_object_depth:
        _error(ConversionErrorCode.PDF_LIMIT_EXCEEDED, "PDF exceeds configured limits.")
    if isinstance(value, IndirectObject):
        key = (value.idnum, value.generation)
        if key in state.seen_indirect:
            return
        state.seen_indirect.add(key)
        state.claim()
        _walk_pdf_object(value.get_object(), state, depth + 1)
        return
    if not isinstance(value, (DictionaryObject, ArrayObject)):
        return
    identity = id(value)
    if identity in state.seen_direct:
        return
    state.seen_direct.add(identity)
    state.claim()
    if isinstance(value, DictionaryObject):
        _walk_pdf_dictionary(value, state, depth)
    else:
        for child in value:
            _walk_pdf_object(child, state, depth + 1)


@contextmanager
def _bounded_pdf_parser(limits: PdfLimits) -> Iterator[None]:
    """Serialize pypdf while applying this call's decoded-stream bounds."""

    with _PDF_PARSE_LOCK:
        previous = {
            name: getattr(pypdf_filters, name) for name in _PDF_FILTER_LIMIT_NAMES
        }
        try:
            for name in _PDF_FILTER_LIMIT_NAMES:
                setattr(pypdf_filters, name, limits.max_pdf_decoded_stream_bytes)
            yield
        finally:
            for name, value in previous.items():
                setattr(pypdf_filters, name, value)


def _validate_pdf(data: bytes, limits: PdfLimits) -> tuple[PdfPage, ...]:
    if not data.startswith(b"%PDF-") or not data.rstrip().endswith(b"%%EOF"):
        _error(ConversionErrorCode.INVALID_PDF, "LibreOffice produced an invalid PDF.")
    try:
        with _bounded_pdf_parser(limits):
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                _error(
                    ConversionErrorCode.INVALID_PDF,
                    "LibreOffice produced an encrypted PDF.",
                )
            _walk_pdf_object(reader.trailer, _PdfWalkState(limits), 0)
            if not reader.pages or len(reader.pages) > limits.max_pages:
                _error(
                    ConversionErrorCode.PDF_LIMIT_EXCEEDED,
                    "PDF exceeds configured limits.",
                )
            pages: list[PdfPage] = []
            for page in reader.pages:
                width = float(page.mediabox.width)
                height = float(page.mediabox.height)
                if not all(
                    math.isfinite(value) and value > 0 for value in (width, height)
                ):
                    _error(
                        ConversionErrorCode.INVALID_PDF,
                        "LibreOffice produced an invalid PDF.",
                    )
                pages.append(PdfPage(width, height))
    except ConversionError:
        raise
    except LimitReachedError:
        _error(ConversionErrorCode.PDF_LIMIT_EXCEEDED, "PDF exceeds configured limits.")
    except AssertionError, OSError, PyPdfError, RecursionError, TypeError, ValueError:
        _error(ConversionErrorCode.INVALID_PDF, "LibreOffice produced an invalid PDF.")
    return tuple(pages)


class LibreOfficePdfConverter:
    """Convert trusted pipeline DOCX bytes to a validated PDF artifact."""

    def __init__(
        self,
        config: LibreOfficeConfig,
        limits: PdfLimits,
        host_environment: Mapping[str, str],
    ) -> None:
        self._config = config
        self._limits = limits
        self._host_environment = dict(host_environment)

    def convert(
        self,
        docx: bytes,
        traceability: PdfTraceabilityContext,
        cancellation_requested: Callable[[], bool] | None = None,
    ) -> PdfArtifact:
        """Convert once; T13 later owns durable cancellation and publication races."""

        cancelled = cancellation_requested or (lambda: False)
        if type(docx) is not bytes or not docx:
            _error(ConversionErrorCode.INVALID_DOCX, "DOCX input is invalid.")
        if len(docx) > self._limits.max_docx_bytes:
            _error(
                ConversionErrorCode.PDF_LIMIT_EXCEEDED,
                "DOCX input exceeds configured limits.",
            )
        if not _safe_docx(docx, self._limits):
            _error(ConversionErrorCode.INVALID_DOCX, "DOCX input is invalid.")
        if self._cancelled(cancelled):
            self._cancelled_error()
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix="md-converter-pdf-", dir=self._config.workspace_root
            )
        except OSError:
            self._workspace_error()
        try:
            result = self._convert_in_workspace(
                Path(temporary.name), docx, traceability, cancelled
            )
        except Exception:
            try:
                temporary.cleanup()
            except OSError:
                self._workspace_error()
            raise
        try:
            temporary.cleanup()
        except OSError:
            self._workspace_error()
        return result

    @staticmethod
    def _cancelled(probe: Callable[[], bool]) -> bool:
        try:
            result = probe()
        except Exception:
            _error(
                ConversionErrorCode.PDF_FAILURE,
                "PDF cancellation probe failed.",
            )
        if type(result) is not bool:
            _error(
                ConversionErrorCode.PDF_FAILURE,
                "PDF cancellation probe failed.",
            )
        return result

    @staticmethod
    def _cancelled_error() -> None:
        _error(ConversionErrorCode.PDF_CANCELLED, "PDF conversion was cancelled.")

    @staticmethod
    def _workspace_error() -> None:
        _error(
            ConversionErrorCode.WORKSPACE_FAILURE,
            "The conversion workspace failed.",
        )

    def _convert_in_workspace(
        self,
        workspace: Path,
        docx: bytes,
        traceability: PdfTraceabilityContext,
        cancelled: Callable[[], bool],
    ) -> PdfArtifact:
        try:
            for directory in (
                "home",
                "tmp",
                "cache",
                "config",
                "data",
                "output",
                "profile",
            ):
                (workspace / directory).mkdir(mode=0o700)
            source = workspace / "source.docx"
            source.write_bytes(docx)
        except OSError:
            self._workspace_error()
        arguments = [
            self._config.executable,
            "--headless",
            "--nologo",
            "--nodefault",
            "--nofirststartwizard",
            f"-env:UserInstallation={(workspace / 'profile').as_uri()}",
            "--convert-to",
            _PDF_EXPORT_FILTER,
            "--outdir",
            str(workspace / "output"),
            str(source),
        ]
        process = self._start(arguments, workspace)
        self._wait(process, cancelled)
        if self._cancelled(cancelled):
            self._cancelled_error()
        pdf = _read_pdf(workspace / "output" / "source.pdf", self._limits.max_pdf_bytes)
        pages = _validate_pdf(pdf, self._limits)
        manifest = PdfTraceabilityManifest(
            schema_version=1,
            application_version=traceability.application_version,
            conversion_contract_version=traceability.conversion_contract_version,
            template_id=traceability.template_id,
            template_version=traceability.template_version,
            template_sha256=traceability.template_sha256,
            source_docx_sha256=hashlib.sha256(docx).hexdigest(),
            output_pdf_sha256=hashlib.sha256(pdf).hexdigest(),
            output_pdf_bytes=len(pdf),
            pages=pages,
            pandoc_version=traceability.pandoc_version,
            pandoc_reader=traceability.pandoc_reader,
            mermaid_version=traceability.mermaid_version,
            chromium_version=traceability.chromium_version,
            libreoffice_version=self._config.version,
            font_manifest_sha256=traceability.font_manifest_sha256,
            export_filter=_PDF_EXPORT_FILTER,
            output_format=_OUTPUT_FORMAT,
        )
        return PdfArtifact(pdf, manifest)

    def _start(self, arguments: list[str], workspace: Path) -> subprocess.Popen[bytes]:
        try:
            return subprocess.Popen(  # noqa: S603 - fixed shell-free arguments
                arguments,
                cwd=workspace,
                env=_environment(workspace, self._host_environment),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
        except OSError:
            _error(
                ConversionErrorCode.LIBREOFFICE_UNAVAILABLE,
                "LibreOffice is unavailable.",
            )

    def _wait(
        self, process: subprocess.Popen[bytes], cancelled: Callable[[], bool]
    ) -> None:
        deadline = time.monotonic() + self._config.timeout_seconds
        while True:
            try:
                cancellation_requested = self._cancelled(cancelled)
            except ConversionError:
                _terminate_group(process, self._config.termination_grace_seconds)
                raise
            if cancellation_requested:
                _terminate_group(process, self._config.termination_grace_seconds)
                self._cancelled_error()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_group(process, self._config.termination_grace_seconds)
                _error(
                    ConversionErrorCode.PDF_TIMEOUT,
                    "PDF conversion timed out.",
                )
            try:
                return_code = process.wait(
                    timeout=min(self._config.cancellation_poll_seconds, remaining)
                )
            except subprocess.TimeoutExpired:
                continue
            _terminate_group(process, self._config.termination_grace_seconds)
            if return_code != 0:
                _error(
                    ConversionErrorCode.PDF_FAILURE,
                    "LibreOffice PDF conversion failed.",
                )
            return
