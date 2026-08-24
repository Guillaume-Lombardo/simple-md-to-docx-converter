"""Isolated Pandoc subprocess adapter for DOCX generation."""

from __future__ import annotations

import io
import math
import os
import signal
import stat
import subprocess
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from md_converter.conversion.archive import ApprovedDocument
from md_converter.conversion.errors import ConversionError, ConversionErrorCode
from md_converter.conversion.validation import (
    PANDOC_READER,
    ApprovedMarkdown,
    validate_document,
)

_REQUIRED_DOCX_PARTS = frozenset(
    {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
)


@dataclass(frozen=True)
class PandocConfig:
    """Explicit adapter configuration without T18 production defaults."""

    executable: str
    timeout_seconds: float
    termination_grace_seconds: float
    workspace_root: Path | None = None

    def __post_init__(self) -> None:
        if not self.executable or "\0" in self.executable:
            raise ValueError("Pandoc executable must be a non-empty path or command")
        for value in (self.timeout_seconds, self.termination_grace_seconds):
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError("Pandoc deadlines must be finite and positive")
        if self.workspace_root is not None and not self.workspace_root.is_dir():
            raise ValueError("Pandoc workspace root must be an existing directory")


def _is_safe_docx(data: bytes) -> bool:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return False
    with archive:
        members = archive.infolist()
        names: set[str] = set()
        for member in members:
            name = member.filename
            path = PurePosixPath(name)
            if (
                not name
                or "\0" in name
                or "\\" in name
                or path.is_absolute()
                or ".." in path.parts
                or stat.S_ISLNK(member.external_attr >> 16)
                or member.flag_bits & 1
                or name in names
            ):
                return False
            names.add(name)
        return names >= _REQUIRED_DOCX_PARTS


class PandocDocxConverter:
    """Run Pandoc with fixed arguments inside a disposable per-call workspace."""

    def __init__(
        self,
        config: PandocConfig,
        host_environment: Mapping[str, str],
    ) -> None:
        self._config = config
        self._host_environment = dict(host_environment)

    def _environment(self, workspace: Path) -> dict[str, str]:
        environment = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
        if "PATH" in self._host_environment:
            environment["PATH"] = self._host_environment["PATH"]
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

    def convert(self, markdown: ApprovedMarkdown, reference_docx: bytes) -> bytes:
        validated = validate_document(
            ApprovedDocument(markdown.text, markdown.entrypoint, markdown.resources)
        )
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix="md-converter-pandoc-", dir=self._config.workspace_root
            )
        except OSError:
            raise self._workspace_failure() from None
        try:
            result = self._convert_in_workspace(
                Path(temporary.name), validated, reference_docx
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

    def _convert_in_workspace(
        self,
        workspace: Path,
        markdown: ApprovedMarkdown,
        reference_docx: bytes,
    ) -> bytes:
        try:
            for directory in ("home", "tmp", "cache", "config", "data"):
                (workspace / directory).mkdir(mode=0o700)
            package_path = workspace / "package"
            package_path.mkdir(mode=0o700)
            markdown_path = package_path / markdown.entrypoint
            markdown_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            reference_path = workspace / "reference.docx"
            output_path = workspace / "output.docx"
            markdown_path.write_text(markdown.text, encoding="utf-8")
            for resource in markdown.resources:
                resource_path = package_path / resource.path
                resource_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                if resource_path.exists() or resource_path.is_symlink():
                    raise OSError
                resource_path.write_bytes(resource.content)
            reference_path.write_bytes(reference_docx)
        except OSError:
            raise self._workspace_failure() from None
        arguments = [
            self._config.executable,
            f"--from={PANDOC_READER}",
            "--to=docx",
            f"--reference-doc={reference_path}",
            f"--resource-path={markdown_path.parent}",
            f"--output={output_path}",
            str(markdown_path),
        ]
        process = self._start(arguments, workspace)
        self._wait(process)
        try:
            if output_path.is_symlink():
                raise OSError
            result = output_path.read_bytes()
        except OSError:
            raise self._workspace_failure() from None
        if not _is_safe_docx(result):
            raise ConversionError(
                ConversionErrorCode.INVALID_DOCX,
                "Pandoc produced an invalid DOCX document.",
            )
        return result

    @staticmethod
    def _workspace_failure() -> ConversionError:
        return ConversionError(
            ConversionErrorCode.WORKSPACE_FAILURE,
            "The conversion workspace failed.",
        )

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
                ConversionErrorCode.PANDOC_UNAVAILABLE,
                "Pandoc is unavailable.",
            ) from None

    def _wait(self, process: subprocess.Popen[bytes]) -> None:
        try:
            return_code = process.wait(timeout=self._config.timeout_seconds)
        except subprocess.TimeoutExpired:
            self._terminate_group(process)
            raise ConversionError(
                ConversionErrorCode.PANDOC_TIMEOUT,
                "Pandoc conversion timed out.",
            ) from None
        if return_code != 0:
            raise ConversionError(
                ConversionErrorCode.PANDOC_FAILURE,
                "Pandoc conversion failed.",
            )

    def _terminate_group(self, process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=self._config.termination_grace_seconds)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait()
