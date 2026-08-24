"""Bounded local Mermaid rendering before document-engine conversion."""

from __future__ import annotations

import io
import json
import math
import os
import re
import signal
import stat
import subprocess
import tempfile
import unicodedata
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Protocol

from markdown_it import MarkdownIt
from markdown_it.token import Token
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin
from PIL import Image, UnidentifiedImageError

from md_converter.conversion.archive import ApprovedDocument, ApprovedResource
from md_converter.conversion.errors import (
    ConversionError,
    ConversionErrorCode,
    validation_error,
)
from md_converter.conversion.images import ImageLimits, normalize_image
from md_converter.conversion.validation import ApprovedMarkdown, validate_document

_GENERATED_DIRECTORY = ".md-converter-mermaid"
_SOURCE_MAP_SIZE = 2
_MARKDOWN = MarkdownIt("commonmark").use(front_matter_plugin).use(footnote_plugin)
_CONFIGURATION_DIRECTIVE = re.compile(r"(?i)%%\s*\{")


class DocumentConverter(Protocol):
    """Downstream document converter contract."""

    def convert(self, markdown: ApprovedMarkdown, reference_docx: bytes) -> bytes: ...


class MermaidRenderer(Protocol):
    """Render one bounded Mermaid source to an untrusted raster payload."""

    def render(self, source: str, max_output_bytes: int) -> bytes: ...


@dataclass(frozen=True)
class MermaidLimits:
    """Explicit diagram bounds whose production values remain owned by T18."""

    max_diagrams: int
    max_source_bytes: int
    max_total_source_bytes: int
    max_output_bytes: int
    max_total_output_bytes: int
    max_document_width_pixels: int
    max_document_height_pixels: int

    def __post_init__(self) -> None:
        values = (
            self.max_diagrams,
            self.max_source_bytes,
            self.max_total_source_bytes,
            self.max_output_bytes,
            self.max_total_output_bytes,
            self.max_document_width_pixels,
            self.max_document_height_pixels,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("Mermaid limits must be positive integers")


@dataclass(frozen=True)
class MermaidConfig:
    """Fixed Mermaid CLI and Chromium subprocess configuration."""

    executable: str
    chromium_executable: str
    timeout_seconds: float
    termination_grace_seconds: float
    viewport_width_pixels: int
    viewport_height_pixels: int
    workspace_root: Path | None = None

    def __post_init__(self) -> None:
        for executable in (self.executable, self.chromium_executable):
            if not executable or "\0" in executable:
                raise ValueError(
                    "Mermaid executables must be non-empty paths or commands"
                )
        for value in (self.timeout_seconds, self.termination_grace_seconds):
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("Mermaid deadlines must be finite and positive")
        for value in (self.viewport_width_pixels, self.viewport_height_pixels):
            if type(value) is not int or value <= 0:
                raise ValueError(
                    "Mermaid viewport dimensions must be positive integers"
                )
        if self.workspace_root is not None and not self.workspace_root.is_dir():
            raise ValueError("Mermaid workspace root must be an existing directory")


@dataclass(frozen=True)
class _Block:
    start_line: int
    end_line: int
    prefix: str
    source: str


def _walk(tokens: list[Token]) -> Iterator[Token]:
    pending = list(reversed(tokens))
    while pending:
        token = pending.pop()
        yield token
        if token.children:
            pending.extend(reversed(token.children))


def _mermaid_blocks(markdown: str) -> tuple[_Block, ...]:
    try:
        tokens = _MARKDOWN.parse(markdown)
    except RecursionError, ValueError:
        raise validation_error("Markdown contains invalid Mermaid blocks.") from None
    lines = markdown.splitlines(keepends=True)
    blocks: list[_Block] = []
    for token in _walk(tokens):
        if token.type != "fence" or token.info.strip().casefold() != "mermaid":
            continue
        if token.map is None or len(token.map) != _SOURCE_MAP_SIZE:
            raise validation_error("Markdown contains invalid Mermaid blocks.")
        start, end = token.map
        if start < 0 or end <= start or end > len(lines):
            raise validation_error("Markdown contains invalid Mermaid blocks.")
        opening = lines[start]
        marker = opening.find(token.markup)
        if marker < 0:
            raise validation_error("Markdown contains invalid Mermaid blocks.")
        blocks.append(_Block(start, end, opening[:marker], token.content))
    blocks.sort(key=lambda block: block.start_line)
    if any(left.end_line > right.start_line for left, right in pairwise(blocks)):
        raise validation_error("Markdown contains invalid Mermaid blocks.")
    return tuple(blocks)


def contains_mermaid(markdown: str) -> bool:
    """Return whether Markdown contains at least one supported Mermaid fence."""

    return bool(_mermaid_blocks(markdown))


def _resource_key(path: PurePosixPath) -> str:
    return unicodedata.normalize("NFC", path.as_posix()).casefold()


def _package_paths_are_distinct(paths: tuple[PurePosixPath, ...]) -> bool:
    keys = {_resource_key(path) for path in paths}
    if len(keys) != len(paths):
        return False
    for path in paths:
        parent = path.parent
        while parent.parts not in {(), (".",)}:
            if _resource_key(parent) in keys:
                return False
            parent = parent.parent
    return True


def _display_attribute(width: int, height: int, limits: MermaidLimits) -> str:
    if (
        width <= limits.max_document_width_pixels
        and height <= limits.max_document_height_pixels
    ):
        return f"width={width}px"
    if (
        limits.max_document_width_pixels * height
        <= limits.max_document_height_pixels * width
    ):
        return f"width={limits.max_document_width_pixels}px"
    return f"height={limits.max_document_height_pixels}px"


def _png_dimensions(source: bytes) -> tuple[int, int]:
    try:
        with Image.open(io.BytesIO(source)) as image:
            if image.format != "PNG":
                raise ValueError
            return image.size
    except UnidentifiedImageError, OSError, SyntaxError, TypeError, ValueError:
        raise ConversionError(
            ConversionErrorCode.INVALID_MERMAID_OUTPUT,
            "Mermaid produced an invalid diagram.",
        ) from None


def _normalized_diagram(
    output: bytes,
    limits: MermaidLimits,
    image_limits: ImageLimits,
) -> bytes:
    if type(output) is not bytes or not output or len(output) > limits.max_output_bytes:
        raise ConversionError(
            ConversionErrorCode.INVALID_MERMAID_OUTPUT,
            "Mermaid produced an invalid diagram.",
        )
    output_limits = ImageLimits(
        limits.max_output_bytes,
        image_limits.max_width_pixels,
        image_limits.max_height_pixels,
        image_limits.max_pixels,
        image_limits.max_svg_elements,
        image_limits.max_svg_depth,
    )
    try:
        normalized = normalize_image(
            PurePosixPath("diagram.png"), output, output_limits
        )
    except ConversionError:
        raise ConversionError(
            ConversionErrorCode.INVALID_MERMAID_OUTPUT,
            "Mermaid produced an invalid diagram.",
        ) from None
    if len(normalized) > limits.max_output_bytes:
        raise ConversionError(
            ConversionErrorCode.INVALID_MERMAID_OUTPUT,
            "Mermaid produced an invalid diagram.",
        )
    return normalized


def render_mermaid(
    markdown: ApprovedMarkdown,
    renderer: MermaidRenderer,
    limits: MermaidLimits,
    fallback_image_limits: ImageLimits | None = None,
) -> ApprovedMarkdown:
    """Replace supported Mermaid fences with bounded approved PNG resources."""

    blocks = _mermaid_blocks(markdown.text)
    if not blocks:
        return markdown
    image_limits = markdown.image_limits or fallback_image_limits
    if image_limits is None:
        raise ConversionError(
            ConversionErrorCode.MERMAID_UNAVAILABLE,
            "Mermaid rendering is unavailable.",
        )
    if len(blocks) > limits.max_diagrams:
        raise validation_error("Document exceeds configured Mermaid limits.")
    encoded_sizes = [len(block.source.encode("utf-8")) for block in blocks]
    if any(not block.source.strip() for block in blocks) or any(
        size > limits.max_source_bytes for size in encoded_sizes
    ):
        raise validation_error("Document exceeds configured Mermaid limits.")
    if sum(encoded_sizes) > limits.max_total_source_bytes:
        raise validation_error("Document exceeds configured Mermaid limits.")
    if any(
        _CONFIGURATION_DIRECTIVE.search(block.source)
        or block.source.lstrip().startswith("---")
        for block in blocks
    ):
        raise validation_error("Document contains unsupported Mermaid configuration.")

    generated_paths = tuple(
        markdown.entrypoint.parent / _GENERATED_DIRECTORY / f"{index:04d}.png"
        for index in range(1, len(blocks) + 1)
    )
    package_paths = (
        markdown.entrypoint,
        *(resource.path for resource in markdown.resources),
        *generated_paths,
    )
    if not _package_paths_are_distinct(package_paths):
        raise validation_error("Document package is invalid.")

    lines = markdown.text.splitlines(keepends=True)
    generated: list[ApprovedResource] = []
    replacements: list[tuple[_Block, str]] = []
    total_raw_output = 0
    total_normalized_output = 0
    for index, (block, path) in enumerate(
        zip(blocks, generated_paths, strict=True), start=1
    ):
        output = renderer.render(block.source, limits.max_output_bytes)
        normalized = _normalized_diagram(output, limits, image_limits)
        total_raw_output += len(output)
        total_normalized_output += len(normalized)
        if (
            total_raw_output > limits.max_total_output_bytes
            or total_normalized_output > limits.max_total_output_bytes
        ):
            raise validation_error("Document exceeds configured Mermaid limits.")
        width, height = _png_dimensions(normalized)
        display_attribute = _display_attribute(width, height, limits)
        relative_path = path.relative_to(markdown.entrypoint.parent).as_posix()
        replacement = (
            f"{block.prefix}![Mermaid diagram {index}]({relative_path})"
            f"{{{display_attribute}}}\n"
        )
        generated.append(ApprovedResource(path, normalized))
        replacements.append((block, replacement))

    for block, replacement in reversed(replacements):
        lines[block.start_line : block.end_line] = [replacement]
    document = ApprovedDocument(
        "".join(lines),
        markdown.entrypoint,
        (*markdown.resources, *generated),
        image_limits,
    )
    return validate_document(document)


class MermaidPreprocessingConverter:
    """Render Mermaid diagrams before delegating to the DOCX engine."""

    def __init__(
        self,
        converter: DocumentConverter,
        renderer: MermaidRenderer,
        limits: MermaidLimits,
        fallback_image_limits: ImageLimits | None = None,
    ) -> None:
        self._converter = converter
        self._renderer = renderer
        self._limits = limits
        self._fallback_image_limits = fallback_image_limits

    def convert(self, markdown: ApprovedMarkdown, reference_docx: bytes) -> bytes:
        rendered = render_mermaid(
            markdown,
            self._renderer,
            self._limits,
            self._fallback_image_limits,
        )
        return self._converter.convert(rendered, reference_docx)


class MermaidCliRenderer:
    """Run the pinned local Mermaid CLI and sandboxed Chromium."""

    def __init__(
        self,
        config: MermaidConfig,
        host_environment: Mapping[str, str],
    ) -> None:
        self._config = config
        self._host_environment = dict(host_environment)

    def _environment(self, workspace: Path) -> dict[str, str]:
        environment = {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TZ": "UTC",
            "HOME": str(workspace / "home"),
            "TMPDIR": str(workspace / "tmp"),
            "XDG_CACHE_HOME": str(workspace / "cache"),
            "XDG_CONFIG_HOME": str(workspace / "config"),
            "XDG_DATA_HOME": str(workspace / "data"),
            "XDG_RUNTIME_DIR": str(workspace / "runtime"),
            "PUPPETEER_SKIP_DOWNLOAD": "true",
            "PUPPETEER_EXECUTABLE_PATH": self._config.chromium_executable,
        }
        if "PATH" in self._host_environment:
            environment["PATH"] = self._host_environment["PATH"]
        return environment

    def render(self, source: str, max_output_bytes: int) -> bytes:
        if type(max_output_bytes) is not int or max_output_bytes <= 0:
            raise ValueError("Mermaid output limit must be a positive integer")
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix="md-converter-mermaid-", dir=self._config.workspace_root
            )
        except OSError:
            raise self._workspace_failure() from None
        try:
            result = self._render_in_workspace(
                Path(temporary.name), source, max_output_bytes
            )
        except Exception:
            try:
                temporary.cleanup()
            except OSError:
                raise self._workspace_failure() from None
            raise
        try:
            temporary.cleanup()
        except OSError:
            raise self._workspace_failure() from None
        return result

    def _render_in_workspace(
        self, workspace: Path, source: str, max_output_bytes: int
    ) -> bytes:
        input_path = workspace / "diagram.mmd"
        output_path = workspace / "diagram.png"
        puppeteer_path = workspace / "puppeteer.json"
        config_path = workspace / "mermaid.json"
        try:
            for directory in ("home", "tmp", "cache", "config", "data", "runtime"):
                (workspace / directory).mkdir(mode=0o700)
            input_path.write_text(source, encoding="utf-8")
            puppeteer_path.write_text(
                json.dumps(
                    {
                        "executablePath": self._config.chromium_executable,
                        "headless": "shell",
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            config_path.write_text(
                json.dumps(
                    {"securityLevel": "strict"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        except OSError:
            raise self._workspace_failure() from None
        arguments = [
            self._config.executable,
            "--quiet",
            "--puppeteerConfigFile",
            str(puppeteer_path),
            "--configFile",
            str(config_path),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--outputFormat",
            "png",
            "--backgroundColor",
            "transparent",
            "--width",
            str(self._config.viewport_width_pixels),
            "--height",
            str(self._config.viewport_height_pixels),
            "--scale",
            "1",
        ]
        process = self._start(arguments, workspace)
        self._wait(process)
        descriptor = -1
        try:
            descriptor = os.open(
                output_path,
                os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > max_output_bytes
            ):
                raise OSError
            remaining = max_output_bytes + 1
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(descriptor, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            result = b"".join(chunks)
            if len(result) > max_output_bytes:
                raise OSError
            return result
        except OSError:
            raise ConversionError(
                ConversionErrorCode.INVALID_MERMAID_OUTPUT,
                "Mermaid produced an invalid diagram.",
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _start(self, arguments: list[str], workspace: Path) -> subprocess.Popen[bytes]:
        try:
            return subprocess.Popen(  # noqa: S603 - fixed arguments and no shell
                arguments,
                cwd=workspace,
                env=self._environment(workspace),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
        except OSError:
            raise ConversionError(
                ConversionErrorCode.MERMAID_UNAVAILABLE,
                "Mermaid rendering is unavailable.",
            ) from None

    def _wait(self, process: subprocess.Popen[bytes]) -> None:
        try:
            return_code = process.wait(timeout=self._config.timeout_seconds)
        except subprocess.TimeoutExpired:
            self._terminate_group(process)
            raise ConversionError(
                ConversionErrorCode.MERMAID_TIMEOUT,
                "Mermaid rendering timed out.",
            ) from None
        if return_code != 0:
            self._terminate_survivors(process.pid)
            raise ConversionError(
                ConversionErrorCode.MERMAID_FAILURE,
                "Mermaid rendering failed.",
            )
        self._terminate_survivors(process.pid)

    @staticmethod
    def _terminate_survivors(process_group: int) -> None:
        try:
            os.killpg(process_group, signal.SIGTERM)
            os.killpg(process_group, 0)
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            return

    def _terminate_group(self, process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=self._config.termination_grace_seconds)
            self._terminate_survivors(process.pid)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=self._config.termination_grace_seconds)
        except subprocess.TimeoutExpired:
            return

    @staticmethod
    def _workspace_failure() -> ConversionError:
        return ConversionError(
            ConversionErrorCode.WORKSPACE_FAILURE,
            "The conversion workspace failed.",
        )
