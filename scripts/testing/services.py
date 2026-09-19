"""Own disposable local PostgreSQL/RustFS services for one test session."""

from __future__ import annotations

import os
import secrets
import subprocess
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from scripts.ci.prepare_s3_test_bucket import main as prepare_bucket

VARIABLES = (
    "MARKWEAVE_TEST_POSTGRES_URL",
    "MARKWEAVE_TEST_S3_ENDPOINT_URL",
    "MARKWEAVE_TEST_S3_REGION",
    "MARKWEAVE_TEST_S3_ACCESS_KEY_ID",
    "MARKWEAVE_TEST_S3_SECRET_ACCESS_KEY",
    "MARKWEAVE_TEST_S3_BUCKET",
)
MAX_PORT = 65535
ROOT = Path(__file__).resolve().parents[2]


def configured_environment(environment: Mapping[str, str]) -> bool:
    """Never silently replace a partially configured external service set."""
    configured = {name for name in VARIABLES if environment.get(name)}
    if configured and configured != set(VARIABLES):
        missing = ", ".join(sorted(set(VARIABLES) - configured))
        raise RuntimeError(
            f"Incomplete distributed test configuration; missing: {missing}"
        )
    return bool(configured)


def _compose(project: str, environment: Mapping[str, str], *arguments: str) -> str:
    command = (
        "docker",
        "--host",
        "unix:///var/run/docker.sock",
        "compose",
        "--project-name",
        project,
        "--file",
        str(ROOT / "compose.test.yaml"),
        *arguments,
    )
    try:
        result = subprocess.run(  # noqa: S603 - fixed local Compose argv
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(
            "Local test services require a working Docker Compose installation; "
            "alternatively configure all MARKWEAVE_TEST_* service variables."
        ) from error
    if result.returncode:
        # Engine diagnostics can contain environment values. Do not echo them.
        raise RuntimeError(f"Local test services: Compose {arguments[0]} failed.")
    return result.stdout.strip()


def _port(value: str) -> int:
    host, separator, port = value.partition(":")
    if host != "127.0.0.1" or not separator or not port.isdecimal():
        raise RuntimeError("Test service must publish one loopback-only port.")
    number = int(port)
    if not 1 <= number <= MAX_PORT:
        raise RuntimeError("Test service port is invalid.")
    return number


@contextmanager
def distributed_services() -> Iterator[None]:
    """Reuse explicit services or acquire, initialize and remove a private test stack."""
    if configured_environment(os.environ):
        yield
        return
    project = f"markweave-tests-{uuid4().hex}"
    password = secrets.token_hex(24)
    access_key = secrets.token_hex(12)
    environment = {
        **os.environ,
        "MARKWEAVE_LOCAL_TEST_PASSWORD": password,
        "MARKWEAVE_LOCAL_TEST_ACCESS_KEY": access_key,
    }
    previous = {name: os.environ.get(name) for name in VARIABLES}
    try:
        _compose(
            project, environment, "up", "--detach", "--wait", "--wait-timeout", "120"
        )
        postgres = _port(_compose(project, environment, "port", "postgres", "5432"))
        rustfs = _port(_compose(project, environment, "port", "rustfs", "9000"))
        os.environ.update(
            dict(
                zip(
                    VARIABLES,
                    (
                        f"postgresql+psycopg://postgres:{password}@127.0.0.1:{postgres}/md_converter_test",
                        f"http://127.0.0.1:{rustfs}",
                        "us-east-1",
                        access_key,
                        password,
                        f"test-{uuid4().hex}",
                    ),
                    strict=True,
                )
            )
        )
        prepare_bucket()
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        _compose(
            project,
            environment,
            "down",
            "--volumes",
            "--remove-orphans",
            "--timeout",
            "10",
        )
