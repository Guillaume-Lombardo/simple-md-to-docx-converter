"""Install and import a manifest-bound wheel in a fresh uv environment."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scripts.release.artifacts import (
    MANIFEST_NAME,
    MAX_ARTIFACT_BYTES,
    READ_CHUNK_BYTES,
    ArtifactError,
    verify_release,
)
from scripts.release.process import run_command

ENVIRONMENT_TIMEOUT_SECONDS = 120
INSTALL_TIMEOUT_SECONDS = 300
IMPORT_TIMEOUT_SECONDS = 60
CONSOLE_TIMEOUT_SECONDS = 60
CONTAINERFILE_NAME = "Containerfile"
FINAL_IMAGE_INSTALL_COMMAND = "uv sync --locked --no-dev --no-editable --extra all"

PUBLIC_IMPORT_CHECK = """\
from importlib.metadata import version
from importlib.util import find_spec
import sys

installed = version(sys.argv[1])
if installed != sys.argv[2]:
    raise SystemExit(f"unexpected installed version: {installed}")
import markweave
from markweave import __version__
if __version__ != sys.argv[2]:
    raise SystemExit(f"unexpected markweave.__version__: {__version__}")
if markweave.__all__ != ["__version__"]:
    raise SystemExit("markweave exposes an unsupported Python API")
if hasattr(markweave, "create_app"):
    raise SystemExit("markweave exposes an unsupported server factory")
if find_spec("md_converter") is not None:
    raise SystemExit("legacy md_converter import remains installed")
"""

EXTRA_IMPORT_CHECK = """\
from importlib.util import find_spec
import sys

for module in sys.argv[1:]:
    if find_spec(module) is None:
        raise SystemExit(f"missing required optional dependency: {module}")
import markweave.app
import markweave.storage
if not callable(markweave.app.create_app):
    raise SystemExit("server install does not expose its internal ASGI factory")
"""

REVERSE_ATTEMPT_IMPORT_CHECK = """\
from importlib.util import find_spec
import sys

for module in sys.argv[1:]:
    if find_spec(module) is None:
        raise SystemExit(f"missing required reverse-attempt dependency: {module}")
from markweave.conversion.images import normalize_image
import markweave.reversions.attempt_main
if not callable(normalize_image):
    raise SystemExit("reverse-attempt image normalization is unavailable")
if find_spec("fastapi") is not None or find_spec("uvicorn") is not None:
    raise SystemExit("reverse-attempt install contains HTTP server dependencies")
"""

BASE_ISOLATION_CHECK = """\
from importlib.util import find_spec
import sys

for module in sys.argv[1:]:
    if find_spec(module) is not None:
        raise SystemExit(f"base install unexpectedly contains optional dependency: {module}")
"""

BASE_RECOVERY_CHECK = """\
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from markweave.cli.main import main

expected = (
    '{"error":{"code":"optional_dependency_missing",'
    '"message":"Recovery commands require server dependencies; '
    "install 'markweave[server]'." + '"}}\\n'
)
commands = (
    ("--json", "--non-interactive", "backup"),
    (
        "--json", "--non-interactive", "restore",
        "--profile", "standalone", "--source", "/recovery-set",
        "--offline-proof", "test-window", "--yes",
    ),
)
for command in commands:
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = main(command)
    if result != 1 or stdout.getvalue() or stderr.getvalue() != expected:
        raise SystemExit(
            f"unexpected base recovery failure: {command!r} "
            f"code={result!r} stdout={stdout.getvalue()!r} stderr={stderr.getvalue()!r}"
        )
"""

STANDALONE_RECOVERY_CHECK = """\
from contextlib import redirect_stderr, redirect_stdout
from importlib.util import find_spec
from io import StringIO
import json
from pathlib import Path
import sys
from uuid import uuid4

for module in ("boto3", "botocore", "psycopg"):
    if find_spec(module) is not None:
        raise SystemExit(f"standalone install contains distributed dependency: {module}")

from markweave.cli.main import main
from markweave.persistence.migrations import upgrade_database
from markweave.persistence.sql import create_database_engine, standalone_database_url
import markweave.recovery_adapters
import markweave.recovery_service

for module in ("boto3", "botocore"):
    if module in sys.modules:
        raise SystemExit(f"standalone recovery imported S3 dependency: {module}")

root = Path.cwd() / "standalone-recovery-contract"
data = root / "data"
object_path = data / "objects" / "uploads" / str(uuid4()) / str(uuid4())
object_path.parent.mkdir(parents=True)
object_path.write_bytes(b"standalone-recovery-object")
engine = create_database_engine(standalone_database_url(data))
try:
    upgrade_database(engine)
finally:
    engine.dispose()

def invoke(command):
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        result = main(command)
    if result != 0 or stderr.getvalue():
        raise SystemExit(
            f"unexpected standalone recovery failure: {command!r} "
            f"code={result!r} stdout={stdout.getvalue()!r} stderr={stderr.getvalue()!r}"
        )
    return json.loads(stdout.getvalue())

backups = root / "backups"
backup = invoke((
    "--json", "--non-interactive", "backup",
    "--profile", "standalone", "--destination", str(backups),
    "--data-directory", str(data),
))
if backup.get("profile") != "standalone" or backup.get("status") != "created":
    raise SystemExit(f"unexpected standalone backup output: {backup!r}")
recovery_set = backups / backup["backup_id"]
restored = root / "restored"
restore = invoke((
    "--json", "--non-interactive", "restore",
    "--profile", "standalone", "--source", str(recovery_set),
    "--data-directory", str(restored), "--offline-proof", "install-check", "--yes",
))
if restore.get("profile") != "standalone" or restore.get("status") != "verified":
    raise SystemExit(f"unexpected standalone restore output: {restore!r}")
if not (restored / "metadata.sqlite3").is_file():
    raise SystemExit("standalone restore did not create the SQLite database")
restored_objects = [path.read_bytes() for path in (restored / "objects").rglob("*") if path.is_file()]
if restored_objects != [b"standalone-recovery-object"]:
    raise SystemExit(f"standalone restore object mismatch: {restored_objects!r}")
"""

DISTRIBUTED_RECOVERY_CHECK = """\
from markweave.recovery_adapters import (
    RecoveryDeadline,
    S3Configuration,
    S3RecoveryAdapter,
)

adapter = S3RecoveryAdapter(
    S3Configuration(
        bucket="install-check",
        endpoint_url="http://127.0.0.1:1",
        region="us-east-1",
        access_key_id="install-check",
        secret_access_key="install-check",
    ),
    RecoveryDeadline.after(5),
)
adapter.close()
"""

BASE_FORBIDDEN_MODULES = ("fastapi", "sqlalchemy", "boto3", "psycopg", "anydoc")


@dataclass(frozen=True)
class InstallationProfile:
    """One supported wheel-installation dependency surface."""

    name: str
    extra: str | None
    required_modules: tuple[str, ...]
    import_check: str = EXTRA_IMPORT_CHECK


_SERVER_MODULES = (
    "alembic",
    "argon2",
    "cairosvg",
    "fastapi",
    "markdown_it",
    "mdit_py_plugins",
    "PIL",
    "pydantic_settings",
    "pypdf",
    "multipart",
    "yaml",
    "sqlalchemy",
    "tinycss2",
    "uvicorn",
)
SUPPORTED_INSTALLATION_PROFILES = (
    InstallationProfile("base", None, ()),
    InstallationProfile("server", "server", _SERVER_MODULES),
    InstallationProfile("standalone", "standalone", _SERVER_MODULES),
    InstallationProfile(
        "distributed", "distributed", (*_SERVER_MODULES, "boto3", "psycopg")
    ),
    InstallationProfile("all", "all", (*_SERVER_MODULES, "boto3", "psycopg")),
    InstallationProfile(
        "reverse-attempt",
        "reverse-attempt",
        ("anydoc", "cairosvg", "defusedxml", "PIL", "tinycss2"),
        REVERSE_ATTEMPT_IMPORT_CHECK,
    ),
)


@dataclass(frozen=True)
class CleanInstallResult:
    """Digest linkage between the installed copy and publishable wheel."""

    wheel_name: str
    sha256: str


def verify_final_image_dependency_union(project_root: Path) -> None:
    """Require the final image to install the complete supported dependency union."""
    containerfile = project_root / CONTAINERFILE_NAME
    try:
        if not containerfile.is_file() or containerfile.is_symlink():
            raise OSError("Containerfile is not a regular file")
        content = containerfile.read_text(encoding="utf-8")
    except OSError as error:
        raise ArtifactError(f"cannot read final image definition: {error}") from error
    if FINAL_IMAGE_INSTALL_COMMAND not in content:
        raise ArtifactError("final image does not install the markweave[all] union")


def _copy_manifest_bound_wheel(source: Path, destination: Path, digest: str) -> None:
    source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        source_descriptor = os.open(source, source_flags)
    except OSError as error:
        raise ArtifactError(f"cannot reopen verified wheel: {error}") from error
    destination_descriptor: int | None = None
    try:
        source_metadata = os.fstat(source_descriptor)
        if not stat.S_ISREG(source_metadata.st_mode):
            raise ArtifactError("verified wheel is no longer a regular file")
        if source_metadata.st_size > MAX_ARTIFACT_BYTES:
            raise ArtifactError("verified wheel exceeds the size limit")
        destination_descriptor = os.open(destination, destination_flags, 0o400)
        copied_digest = hashlib.sha256()
        copied_size = 0
        while chunk := os.read(source_descriptor, READ_CHUNK_BYTES):
            copied_size += len(chunk)
            if copied_size > MAX_ARTIFACT_BYTES:
                raise ArtifactError("verified wheel changed beyond the size limit")
            copied_digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise ArtifactError("private wheel copy made no progress")
                view = view[written:]
        os.fsync(destination_descriptor)
        if (
            copied_size != source_metadata.st_size
            or copied_digest.hexdigest() != digest
        ):
            raise ArtifactError("verified wheel changed before private copy completed")
    except OSError as error:
        raise ArtifactError(f"private wheel copy failed: {error}") from error
    finally:
        os.close(source_descriptor)
        if destination_descriptor is not None:
            os.close(destination_descriptor)


def verify_clean_install(
    directory: Path,
    *,
    expected_name: str,
    expected_version: str,
    manifest_name: str = MANIFEST_NAME,
    project_root: Path | None = None,
) -> CleanInstallResult:
    """Verify integrity and every supported clean wheel-installation profile."""
    artifacts = verify_release(
        directory,
        expected_name=expected_name,
        expected_version=expected_version,
        manifest_name=manifest_name,
    )
    wheel_digest = artifacts.sha256_for(artifacts.wheel)
    uv = shutil.which("uv")
    if uv is None:
        raise ArtifactError("uv executable was not found")
    verify_final_image_dependency_union(project_root or Path.cwd())

    with tempfile.TemporaryDirectory(prefix="md-converter-wheel-") as temporary:
        root = Path(temporary)
        private_artifacts = root / "artifacts"
        private_artifacts.mkdir(mode=0o700)
        private_wheel = private_artifacts / artifacts.wheel.name
        _copy_manifest_bound_wheel(artifacts.wheel, private_wheel, wheel_digest)
        for profile in SUPPORTED_INSTALLATION_PROFILES:
            environment = root / f"venv-{profile.name}"
            python = environment / "bin" / "python"
            run_command(
                (
                    uv,
                    "venv",
                    "--python",
                    "3.14",
                    "--no-project",
                    str(environment),
                ),
                cwd=root,
                label=f"clean {profile.name} environment creation",
                timeout=ENVIRONMENT_TIMEOUT_SECONDS,
            )
            requirement = str(private_wheel)
            if profile.extra is not None:
                requirement = f"{requirement}[{profile.extra}]"
            run_command(
                (
                    uv,
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    "--strict",
                    requirement,
                ),
                cwd=root,
                label=f"exact {profile.name} wheel installation",
                timeout=INSTALL_TIMEOUT_SECONDS,
            )
            run_command(
                (
                    str(python),
                    "-I",
                    "-c",
                    PUBLIC_IMPORT_CHECK,
                    expected_name,
                    expected_version,
                ),
                cwd=root,
                label=f"isolated {profile.name} public import check",
                timeout=IMPORT_TIMEOUT_SECONDS,
            )
            if profile.name == "base":
                run_command(
                    (
                        str(python),
                        "-I",
                        "-c",
                        BASE_ISOLATION_CHECK,
                        *BASE_FORBIDDEN_MODULES,
                    ),
                    cwd=root,
                    label="base optional dependency isolation check",
                    timeout=IMPORT_TIMEOUT_SECONDS,
                )
                run_command(
                    (str(python), "-I", "-c", BASE_RECOVERY_CHECK),
                    cwd=root,
                    label="base recovery optional dependency error check",
                    timeout=CONSOLE_TIMEOUT_SECONDS,
                )
            elif profile.required_modules:
                run_command(
                    (
                        str(python),
                        "-I",
                        "-c",
                        profile.import_check,
                        *profile.required_modules,
                    ),
                    cwd=root,
                    label=f"isolated {profile.name} optional dependency check",
                    timeout=IMPORT_TIMEOUT_SECONDS,
                )
                if profile.name == "standalone":
                    run_command(
                        (str(python), "-I", "-c", STANDALONE_RECOVERY_CHECK),
                        cwd=root,
                        label="standalone recovery success and S3 isolation check",
                        timeout=CONSOLE_TIMEOUT_SECONDS,
                    )
                elif profile.name in {"distributed", "all"}:
                    run_command(
                        (str(python), "-I", "-c", DISTRIBUTED_RECOVERY_CHECK),
                        cwd=root,
                        label=f"{profile.name} recovery S3 dependency check",
                        timeout=CONSOLE_TIMEOUT_SECONDS,
                    )
            console = environment / "bin" / "markweave"
            for arguments, label in (
                (
                    (str(python), "-I", str(console), "--version"),
                    f"isolated {profile.name} console version check",
                ),
                (
                    (str(python), "-I", str(console), "--help"),
                    f"isolated {profile.name} console help check",
                ),
            ):
                run_command(
                    arguments,
                    cwd=root,
                    label=label,
                    timeout=CONSOLE_TIMEOUT_SECONDS,
                )
    return CleanInstallResult(wheel_name=artifacts.wheel.name, sha256=wheel_digest)


def main(argv: list[str] | None = None) -> int:
    """Verify an exact wheel through a clean installation and public import."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--manifest-name", default=MANIFEST_NAME)
    args = parser.parse_args(argv)
    try:
        result = verify_clean_install(
            args.directory,
            expected_name=args.name,
            expected_version=args.version,
            manifest_name=args.manifest_name,
        )
    except ArtifactError as error:
        parser.exit(1, f"error: {error}\n")
    print(f"verified-wheel-sha256={result.sha256} wheel={result.wheel_name}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by CI
    raise SystemExit(main())
