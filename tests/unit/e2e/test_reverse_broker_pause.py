"""Bound the test-only exact-unit pause barrier and its failure cleanup."""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from scripts.e2e import reverse_broker as fixture

pytestmark = pytest.mark.unit


def _setup(tmp_path: Path, mocker: MockerFixture):
    attempt, unit_id, job = uuid4(), uuid4(), uuid4()
    incarnation, specification = object(), object()
    unit = SimpleNamespace(
        unit_id=unit_id,
        attempt_id=attempt,
        principal=SimpleNamespace(principal_id=fixture.WORKER_ID),
        policy_revision=fixture.POLICY,
        policy_specification=specification,
        runtime_incarnation=incarnation,
    )
    config = SimpleNamespace(
        state_directory=tmp_path,
        authentication_key=b"x" * 32,
        max_units=16,
        policy=object(),
        podman_limits=object(),
        image_repository="localhost/fixture",
        hooks_directory=tmp_path,
    )
    mocker.patch.object(fixture, "load_broker_process_config", return_value=config)
    mocker.patch.object(
        fixture, "policy_specification_evidence", return_value=specification
    )
    inventory = mocker.Mock()
    inventory.unacknowledged.side_effect = [(), (unit,)]
    inventory.unit = unit
    mocker.patch.object(fixture, "SQLiteBrokerInventory", return_value=inventory)
    mocker.patch.object(fixture, "_runtime_environment", return_value=({}, {}))
    command = mocker.Mock(return_value=(0, b""))
    mocker.patch.object(fixture, "BoundedCommandRunner", return_value=command)
    runtime = mocker.Mock()
    observed = SimpleNamespace(
        unit_id=unit_id,
        attempt_id=attempt,
        principal_id=fixture.WORKER_ID,
        incarnation=incarnation,
        container_id="a" * 64,
    )
    runtime.discover.return_value = (observed,)
    runtime._inspect.return_value = {"State": {"Paused": True}}
    mocker.patch.object(fixture, "PodmanIsolationRuntime", return_value=runtime)
    state = tmp_path / "state.json"
    state_value = {
        "schema": "t73-reverse-lifecycle-v1",
        "recovery_job_id": str(job),
        "profile": "standalone",
        "scenario": "worker-restart",
        "job_ids": [str(job)],
    }
    state.write_text(json.dumps(state_value))
    binding = tmp_path / "binding.json"
    binding.write_text(
        json.dumps(
            {
                **state_value,
                "schema": "t73-reverse-attempt-diagnostics-v1",
                "attempts": [
                    {
                        "job_id": str(job),
                        "attempt_id": str(attempt),
                        "attempt_number": 1,
                    }
                ],
            }
        )
    )
    binding.with_suffix(".ready").touch()
    barrier = tmp_path / "paused.json"
    barrier.with_suffix(".release").touch()
    return state, binding, barrier, command, runtime, observed, inventory


def test_pause_is_bound_to_exact_synthetic_attempt_and_resumed(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, _runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )
    fixture.watch_pause(tmp_path, state, binding, barrier)
    assert json.loads(barrier.read_text())["container_id"] == observed.container_id
    assert command.call_args_list[0].args == (("pause", observed.container_id),)
    assert command.call_args_list[-1].args == (("unpause", observed.container_id),)


def test_binding_mismatch_unpauses_without_crash_marker(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, _runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )
    value = json.loads(binding.read_text())
    value["attempts"] = []
    binding.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="not the synthetic"):
        fixture.watch_pause(tmp_path, state, binding, barrier)
    assert not barrier.exists()
    command.assert_any_call(("unpause", observed.container_id))


def test_runtime_incarnation_mismatch_never_pauses(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, _runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )
    observed.incarnation = object()
    with pytest.raises(RuntimeError, match="binding differs"):
        fixture.watch_pause(tmp_path, state, binding, barrier)
    command.assert_not_called()
    assert not barrier.exists()


def test_pause_deadline_unpauses_exact_owned_unit(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, _runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )
    barrier.with_suffix(".release").unlink()
    mocker.patch.object(fixture.time, "monotonic", side_effect=[0, 0, 31])
    with pytest.raises(RuntimeError, match="release timed out"):
        fixture.watch_pause(tmp_path, state, binding, barrier)
    command.assert_any_call(("unpause", observed.container_id))


@pytest.mark.parametrize("field", ["schema", "profile", "scenario", "job_ids"])
def test_diagnostic_source_mismatch_unpauses(
    tmp_path: Path, mocker: MockerFixture, field: str
) -> None:
    state, binding, barrier, command, _runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )
    value = json.loads(binding.read_text())
    value[field] = "wrong"
    binding.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="source binding differs"):
        fixture.watch_pause(tmp_path, state, binding, barrier)
    assert not barrier.exists()
    command.assert_any_call(("unpause", observed.container_id))


@pytest.mark.parametrize(
    "field", ["principal", "policy_revision", "policy_specification"]
)
def test_foreign_policy_or_principal_never_pauses(
    tmp_path: Path, mocker: MockerFixture, field: str
) -> None:
    state, binding, barrier, command, _runtime, _observed, inventory = _setup(
        tmp_path, mocker
    )
    replacement = (
        SimpleNamespace(principal_id=uuid4()) if field == "principal" else "foreign"
    )
    setattr(inventory.unit, field, replacement)
    mocker.patch.object(fixture.time, "monotonic", side_effect=[0, 0, 31])
    mocker.patch.object(fixture.time, "sleep")
    with pytest.raises(RuntimeError, match="pause timed out"):
        fixture.watch_pause(tmp_path, state, binding, barrier)
    command.assert_not_called()
    assert not barrier.exists()


def test_observer_termination_resumes_owned_unit(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, _runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )
    mocker.patch.object(
        fixture,
        "_await_binding",
        side_effect=lambda *_args: signal.raise_signal(signal.SIGTERM),
    )
    with pytest.raises(InterruptedError, match="observer interrupted"):
        fixture.watch_pause(tmp_path, state, binding, barrier)
    assert not barrier.exists()
    command.assert_any_call(("unpause", observed.container_id))


@pytest.mark.parametrize("signal_name", ["TERM", "KILL"])
def test_signal_targets_pidfd_of_exact_broker_child(
    tmp_path: Path, mocker: MockerFixture, signal_name: str
) -> None:
    parent, child, descriptor = 42, 43, 99
    expected = ["-m", "markweave.broker.process", str(tmp_path / "broker.json")]
    argv = mocker.patch.object(
        fixture,
        "_process_argv",
        side_effect=[["uv", "run", "python", *expected], [sys.executable, *expected]],
    )
    mocker.patch.object(fixture, "_proc_read", return_value=b"43 ")
    opened = mocker.patch.object(fixture.os, "pidfd_open", return_value=descriptor)
    sent = mocker.patch.object(fixture.signal, "pidfd_send_signal")
    closed = mocker.patch.object(fixture.os, "close")
    fixture.signal_broker(tmp_path, parent, signal_name)
    opened.assert_called_once_with(child)
    argv.assert_any_call(child, parent=parent)
    sent.assert_called_once_with(descriptor, getattr(signal, f"SIG{signal_name}"))
    closed.assert_called_once_with(descriptor)


@pytest.mark.parametrize("fault", ["wrapper", "children", "child"])
def test_signal_rejects_unbound_process(
    tmp_path: Path, mocker: MockerFixture, fault: str
) -> None:
    expected = ["-m", "markweave.broker.process", str(tmp_path / "broker.json")]
    parent = ["uv", "run", "python", *expected]
    child = [sys.executable, *expected]
    if fault == "wrapper":
        parent[-1] = "/unrelated/broker.json"
    if fault == "child":
        child[-1] = "/unrelated/broker.json"
    mocker.patch.object(fixture, "_process_argv", side_effect=[parent, child])
    mocker.patch.object(
        fixture, "_proc_read", return_value=b"43 44" if fault == "children" else b"43"
    )
    opened = mocker.patch.object(fixture.os, "pidfd_open", return_value=99)
    sent = mocker.patch.object(fixture.signal, "pidfd_send_signal")
    closed = mocker.patch.object(fixture.os, "close")
    with pytest.raises(RuntimeError):
        fixture.signal_broker(tmp_path, 42, "KILL")
    sent.assert_not_called()
    if fault == "child":
        closed.assert_called_once_with(99)
    else:
        opened.assert_not_called()


@pytest.mark.parametrize("fault", ["uid", "parent"])
def test_process_identity_rejects_foreign_owner_or_parent(
    mocker: MockerFixture, fault: str
) -> None:
    uid = fixture.os.geteuid()
    actual_uid = uid + 1 if fault == "uid" else uid
    actual_parent = 999 if fault == "parent" else 42
    metadata = f"Uid:\t{actual_uid}\t{actual_uid}\t{actual_uid}\t{actual_uid}\nPPid:\t{actual_parent}\n".encode()
    mocker.patch.object(fixture, "_proc_read", return_value=metadata)
    with pytest.raises(RuntimeError):
        fixture._process_argv(43, parent=42)
