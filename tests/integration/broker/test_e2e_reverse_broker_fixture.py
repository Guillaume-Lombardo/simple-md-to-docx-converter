"""Actual ephemeral certificate and owner-only fixture preparation for T73."""

import contextlib
import json
import os
import signal
import stat
import subprocess
import time
from pathlib import Path

import pytest

from markweave.broker.process import load_broker_process_config
from scripts.e2e.reverse_broker import (
    CHANNEL_LIMITS,
    POLICY,
    SERVER_ID,
    WORKER_ID,
    _process_argv,
    _process_children,
    prepare,
)


@pytest.mark.integration
def test_e2e_broker_fixture_preserves_private_material_and_fixed_policy(
    tmp_path: Path,
) -> None:
    root = tmp_path / "broker"
    digest = "sha256:" + "a" * 64
    prepare(root, "localhost/md-converter-reverse-attempt", digest, "169.254.1.2")
    configuration = load_broker_process_config(root / "broker.json")
    assert configuration.policy.image_digest == digest
    assert configuration.policy.revision == POLICY
    assert configuration.principal.principal_id == WORKER_ID
    assert configuration.mtls_local_identity is not None
    assert configuration.mtls_local_identity.principal.principal_id == SERVER_ID
    assert (
        configuration.policy.channel_limits.max_input_bytes
        == CHANNEL_LIMITS["max_input_bytes"]
    )
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "worker").stat().st_mode) == 0o700
    assert {path.name for path in (root / "worker").iterdir()} == {
        "ca.crt",
        "client.crt",
        "client.key",
    }
    for path in (root / "worker").iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_bytes() == (root / "tls" / path.name).read_bytes()
    settings = dict(
        line.split("=", 1) for line in (root / "worker.env").read_text().splitlines()
    )
    assert (
        settings["MARKWEAVE_REVERSION_BROKER_PRIVATE_KEY_PATH"]
        == "/run/reverse-client/client.key"
    )
    assert settings["MARKWEAVE_REVERSION_BROKER_ENDPOINT_HOST"] == "169.254.1.2"
    pins = json.loads(settings["MARKWEAVE_REVERSION_BROKER_SERVER_LEAF_SHA256"])
    assert len(pins) == 1 and pins[0].startswith("sha256:")
    assert str(root) not in (root / "worker.env").read_text()


@pytest.mark.integration
@pytest.mark.parametrize("crash", [False, True])
def test_uv_child_is_found_across_supervisor_threads(
    tmp_path: Path, crash: bool
) -> None:
    ready = tmp_path / "child.pid"
    program = (
        "import os,time;from pathlib import Path;Path("
        + repr(str(ready))
        + ").write_text(str(os.getpid()));time.sleep(30)"
    )
    process = subprocess.Popen(
        ["uv", "run", "python", "-c", program],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            assert process.poll() is None
            time.sleep(0.01)
        assert ready.exists()
        child = int(ready.read_text())
        assert _process_children(process.pid) == {child}
        assert _process_argv(child, parent=process.pid)[-2:] == ["-c", program]
        if crash:
            descriptor = os.pidfd_open(child)
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            finally:
                os.close(descriptor)
            assert process.wait(timeout=5) == 137
            assert not Path(f"/proc/{child}").exists()
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        finally:
            # The supervisor may exit before a surviving fixture child does.
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
