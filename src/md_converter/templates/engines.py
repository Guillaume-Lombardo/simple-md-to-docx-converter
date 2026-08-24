"""Isolated Pandoc and LibreOffice probes for validated Word templates."""

from __future__ import annotations

import math
import os
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from md_converter.conversion.validation import PANDOC_READER
from md_converter.templates.errors import (
    TemplateValidationError,
    TemplateValidationErrorCode,
)
from md_converter.templates.validation import (
    FontPolicy,
    TemplateFontDeclaration,
    TemplateLimits,
    ValidatedTemplate,
    _validate_template,
    validate_template,
)


@dataclass(frozen=True)
class TemplateEngineConfig:
    """Explicit engine paths and bounds without T18 production defaults."""

    pandoc_executable: str
    libreoffice_executable: str
    timeout_seconds: float
    termination_grace_seconds: float
    workspace_root: Path | None = None

    def __post_init__(self) -> None:
        if any(
            not executable or "\0" in executable
            for executable in (self.pandoc_executable, self.libreoffice_executable)
        ):
            raise ValueError("Template engine executables must be non-empty")
        for value in (self.timeout_seconds, self.termination_grace_seconds):
            if (
                type(value) not in {int, float}
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(
                    "Template engine deadlines must be finite and positive"
                )
        if self.workspace_root is not None and not self.workspace_root.is_dir():
            raise ValueError(
                "Template engine workspace root must be an existing directory"
            )


@dataclass(frozen=True)
class TemplateActivationContext:
    """Static policy and engine dependencies for one activation validation."""

    limits: TemplateLimits
    policy: FontPolicy
    engines: TemplateEngineConfig
    host_environment: Mapping[str, str]


def _engine_error(code: TemplateValidationErrorCode, message: str) -> None:
    raise TemplateValidationError(code, message)


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
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        return
    process.wait()


def _run(
    arguments: Sequence[str],
    workspace: Path,
    environment: Mapping[str, str],
    config: TemplateEngineConfig,
) -> None:
    try:
        process = subprocess.Popen(  # noqa: S603 - fixed, shell-free argv
            list(arguments),
            cwd=workspace,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError, PermissionError:
        _engine_error(
            TemplateValidationErrorCode.ENGINE_UNAVAILABLE,
            "Template validation engine is unavailable.",
        )
    except OSError:
        _engine_error(
            TemplateValidationErrorCode.ENGINE_FAILURE,
            "Template validation engine failed.",
        )
    try:
        return_code = process.wait(timeout=config.timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate_group(process, config.termination_grace_seconds)
        _engine_error(
            TemplateValidationErrorCode.ENGINE_TIMEOUT,
            "Template validation engine timed out.",
        )
    if return_code != 0:
        _engine_error(
            TemplateValidationErrorCode.ENGINE_FAILURE,
            "Template validation engine failed.",
        )


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


def _bounded_regular_file(path: Path, limit: int) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > limit
        ):
            raise OSError
        chunks: list[bytes] = []
        total = 0
        while total <= limit:
            chunk = os.read(descriptor, min(64 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError:
        _engine_error(
            TemplateValidationErrorCode.ENGINE_FAILURE,
            "Template validation engine produced invalid output.",
        )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    data = b"".join(chunks)
    if len(data) != metadata.st_size or len(data) > limit:
        _engine_error(
            TemplateValidationErrorCode.ENGINE_FAILURE,
            "Template validation engine produced invalid output.",
        )
    return data


def validate_template_for_activation(
    data: bytes,
    declaration: TemplateFontDeclaration,
    context: TemplateActivationContext,
) -> ValidatedTemplate:
    """Run static policy, blank Pandoc conversion, and LibreOffice open/save in order."""

    limits, policy, config = context.limits, context.policy, context.engines
    validated = validate_template(data, declaration, limits, policy)
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="md-converter-template-", dir=config.workspace_root
        )
    except OSError:
        _engine_error(
            TemplateValidationErrorCode.ENGINE_FAILURE,
            "Template validation workspace is unavailable.",
        )
    try:
        workspace = Path(temporary.name)
        for directory in (
            "home",
            "tmp",
            "cache",
            "config",
            "data",
            "pandoc",
            "libreoffice",
        ):
            (workspace / directory).mkdir(mode=0o700)
        reference = workspace / "reference.docx"
        blank = workspace / "blank.md"
        canonical = workspace / "pandoc" / "canonical.docx"
        reference.write_bytes(data)
        blank.write_bytes(b"")
        environment = _environment(workspace, context.host_environment)
        _run(
            (
                config.pandoc_executable,
                f"--from={PANDOC_READER}",
                "--to=docx",
                f"--reference-doc={reference}",
                f"--output={canonical}",
                str(blank),
            ),
            workspace,
            environment,
            config,
        )
        canonical_data = _bounded_regular_file(canonical, limits.max_archive_bytes)
        validate_template(canonical_data, declaration, limits, policy)
        profile = workspace / "libreoffice-profile"
        profile.mkdir(mode=0o700)
        _run(
            (
                config.libreoffice_executable,
                "--headless",
                "--nologo",
                "--nodefault",
                "--nofirststartwizard",
                f"-env:UserInstallation={profile.as_uri()}",
                "--convert-to",
                "docx",
                "--outdir",
                str(workspace / "libreoffice"),
                str(canonical),
            ),
            workspace,
            environment,
            config,
        )
        opened = _bounded_regular_file(
            workspace / "libreoffice" / "canonical.docx", limits.max_archive_bytes
        )
        # LibreOffice adds application-default font names while opening/saving even
        # when those fonts are neither installed nor used by the source template.
        # Reapply every structural, active-content, relationship, and style check,
        # but keep the source candidate as the authoritative font contract.
        _validate_template(
            opened,
            declaration,
            limits,
            policy,
            enforce_referenced_fonts=False,
        )
    except OSError:
        _engine_error(
            TemplateValidationErrorCode.ENGINE_FAILURE,
            "Template validation workspace failed.",
        )
    finally:
        try:
            temporary.cleanup()
        except OSError:
            _engine_error(
                TemplateValidationErrorCode.ENGINE_FAILURE,
                "Template validation workspace cleanup failed.",
            )
    return validated
