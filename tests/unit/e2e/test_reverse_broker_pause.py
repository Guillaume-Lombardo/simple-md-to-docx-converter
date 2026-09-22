"""Bound the test-only exact-unit pause barrier and its failure cleanup."""

from __future__ import annotations

import json
import signal
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture

from scripts.e2e import reverse_broker as fixture

pytestmark = pytest.mark.unit


def _setup(tmp_path: Path, mocker: MockerFixture):
    attempt, unit_id, job = uuid4(), uuid4(), uuid4()
    other_attempt, other_unit_id = uuid4(), uuid4()
    incarnation, specification = object(), object()
    unit = SimpleNamespace(
        unit_id=unit_id,
        attempt_id=attempt,
        principal=SimpleNamespace(principal_id=fixture.WORKER_ID),
        policy_revision=fixture.POLICY,
        policy_specification=specification,
        runtime_incarnation=incarnation,
    )
    other_unit = SimpleNamespace(
        unit_id=other_unit_id,
        attempt_id=other_attempt,
        principal=SimpleNamespace(principal_id=fixture.WORKER_ID),
        policy_revision=fixture.POLICY,
        policy_specification=specification,
        runtime_incarnation=object(),
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
    inventory.unacknowledged.side_effect = [(), (other_unit, unit)]
    inventory.unit = unit
    inventory.other_unit = other_unit
    mocker.patch.object(fixture, "SQLiteBrokerInventory", return_value=inventory)
    mocker.patch.object(fixture, "_runtime_environment", return_value=({}, {}))
    runtime_state = {
        "Status": "running",
        "Running": True,
        "Paused": False,
        "ExitCode": 0,
    }

    def run_command(arguments, **_kwargs):
        if arguments[0] == "pause":
            runtime_state["Paused"] = True
        elif arguments[0] == "unpause":
            runtime_state["Paused"] = False
        return 0, b""

    command = mocker.Mock(side_effect=run_command)
    mocker.patch.object(fixture, "BoundedCommandRunner", return_value=command)
    runtime = mocker.Mock()
    observed = SimpleNamespace(
        unit_id=unit_id,
        attempt_id=attempt,
        principal_id=fixture.WORKER_ID,
        incarnation=incarnation,
        container_id="a" * 64,
    )
    other_observed = SimpleNamespace(
        unit_id=other_unit_id,
        attempt_id=other_attempt,
        principal_id=fixture.WORKER_ID,
        incarnation=other_unit.runtime_incarnation,
        container_id="b" * 64,
    )
    runtime.discover.side_effect = [(), (other_observed, observed)]
    runtime._inspect.side_effect = lambda _identity: {"State": dict(runtime_state)}
    runtime.state = runtime_state
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

    def discover(*, limit: int):
        assert limit == 16
        if _runtime.discover.call_count == 1:
            assert not barrier.with_suffix(".ready").exists()
            return ()
        return (_runtime.other_observed, observed)

    _runtime.other_observed = SimpleNamespace(
        unit_id=_inventory.other_unit.unit_id,
        attempt_id=_inventory.other_unit.attempt_id,
        principal_id=fixture.WORKER_ID,
        incarnation=_inventory.other_unit.runtime_incarnation,
        container_id="b" * 64,
    )
    _runtime.discover.side_effect = discover
    fixture.watch_pause(
        tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
    )
    assert json.loads(barrier.read_text())["container_id"] == observed.container_id
    assert _runtime.discover.call_count == 2
    assert command.call_args_list[0].args == (("pause", observed.container_id),)
    assert all("b" * 64 not in call.args[0] for call in command.call_args_list)
    assert command.call_args_list[-1].args == (("unpause", observed.container_id),)
    evidence = json.loads((tmp_path / "pause-state.json").read_text())
    timings = evidence.pop("timings_millis")
    assert evidence == {
        "failure": None,
        "inventory_incarnation": str(_inventory.unit.runtime_incarnation),
        "inventory_unit_id": str(_inventory.unit.unit_id),
        "paused": True,
        "phase": "paused",
        "runtime_attempt_id": str(observed.attempt_id),
        "runtime_container_id": observed.container_id,
        "runtime_incarnation": str(observed.incarnation),
        "runtime_state_before_pause": {
            "exit_code": 0,
            "paused": False,
            "running": True,
            "status": "running",
        },
        "runtime_state_on_failure": None,
        "schema": "t73-reverse-pause-state-v1",
        "target_attempt_id": str(observed.attempt_id),
    }
    assert set(timings) == {
        "prearmed",
        "binding_resolved",
        "inventory_observed",
        "runtime_verified",
        "paused",
        "failed",
    }
    assert all(
        type(timings[field]) is int
        for field in (
            "prearmed",
            "binding_resolved",
            "inventory_observed",
            "runtime_verified",
            "paused",
        )
    )
    assert timings["failed"] is None


def test_binding_mismatch_never_pauses_or_writes_crash_marker(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, _runtime, _observed, _inventory = _setup(
        tmp_path, mocker
    )
    value = json.loads(binding.read_text())
    value["attempts"] = []
    binding.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="not unique"):
        fixture.watch_pause(
            tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
        )
    assert not barrier.exists()
    command.assert_not_called()


def test_preflight_failure_never_announces_or_pauses(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, runtime, _observed, _inventory = _setup(
        tmp_path, mocker
    )
    runtime.discover.side_effect = RuntimeError("runtime discovery failed")

    with pytest.raises(RuntimeError, match="runtime discovery failed"):
        fixture.watch_pause(
            tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
        )

    assert not barrier.with_suffix(".ready").exists()
    assert not barrier.exists()
    command.assert_not_called()


def test_runtime_incarnation_mismatch_never_pauses(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, _runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )
    observed.incarnation = object()
    with pytest.raises(RuntimeError, match="binding differs"):
        fixture.watch_pause(
            tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
        )
    command.assert_not_called()
    assert not barrier.exists()
    evidence = json.loads((tmp_path / "pause-state.json").read_text())
    assert evidence["phase"] == "failed"
    assert evidence["failure"] == "RuntimeError"
    assert evidence["target_attempt_id"] == str(observed.attempt_id)
    assert evidence["runtime_container_id"] is None


def test_pause_deadline_unpauses_exact_owned_unit(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, _runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )
    barrier.with_suffix(".release").unlink()
    mocker.patch.object(fixture.time, "monotonic", side_effect=[0, 0, 31])
    with pytest.raises(RuntimeError, match="release timed out"):
        fixture.watch_pause(
            tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
        )
    command.assert_any_call(("unpause", observed.container_id))
    evidence = json.loads((tmp_path / "pause-state.json").read_text())
    assert evidence["runtime_state_on_failure"] == {
        "exit_code": 0,
        "paused": True,
        "running": True,
        "status": "running",
    }


def test_cleanup_failure_preserves_observer_failure(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )
    barrier.with_suffix(".release").unlink()
    mocker.patch.object(fixture.time, "monotonic", side_effect=[0, 0, 31])

    def fail_cleanup(arguments, **_kwargs):
        if arguments[0] == "pause":
            runtime.state["Paused"] = True
            return 0, b""
        if arguments[:2] == ("container", "exists"):
            raise RuntimeError("cleanup inspection failed")
        return 0, b""

    command.side_effect = fail_cleanup
    with pytest.raises(ExceptionGroup) as raised:
        fixture.watch_pause(
            tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
        )
    assert [str(error) for error in raised.value.exceptions] == [
        "T73 pause barrier release timed out",
        "cleanup inspection failed",
    ]
    assert json.loads((tmp_path / "pause-state.json").read_text())["failure"] == (
        "RuntimeError"
    )
    assert observed.container_id == "a" * 64


@pytest.mark.parametrize(
    ("post_failure", "diagnostic_failure"),
    [("exited", False), ("absent", False), ("exited", True)],
)
def test_pause_failure_preserves_original_and_bounded_state(
    tmp_path: Path,
    mocker: MockerFixture,
    post_failure: str,
    diagnostic_failure: bool,
) -> None:
    state, binding, barrier, command, runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )
    pause_error = RuntimeError("pause command failed")

    def fail_pause(arguments, **_kwargs):
        if arguments[0] == "pause":
            if post_failure == "exited":
                runtime.state.update(
                    Status="exited", Running=False, Paused=False, ExitCode=125
                )
            raise pause_error
        if arguments[:2] == ("container", "exists"):
            return 1, b""
        return 0, b""

    command.side_effect = fail_pause
    original_inspect = runtime._inspect.side_effect
    if post_failure == "absent":
        runtime._inspect.side_effect = [
            original_inspect(observed.container_id),
            RuntimeError("container absent"),
        ]
    if diagnostic_failure:
        original_write = fixture._write_bounded_json
        write_count = 0

        def fail_second_write(path, value):
            nonlocal write_count
            write_count += 1
            if write_count == 2:
                raise OSError("diagnostic write failed")
            original_write(path, value)

        mocker.patch.object(fixture, "_write_bounded_json", fail_second_write)

    with pytest.raises(RuntimeError) as raised:
        fixture.watch_pause(
            tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
        )

    assert raised.value is pause_error
    assert not barrier.exists()
    assert all("b" * 64 not in call.args[0] for call in command.call_args_list)
    evidence = json.loads((tmp_path / "pause-state.json").read_text())
    if diagnostic_failure:
        assert evidence["phase"] == "runtime-verified"
    else:
        assert evidence["phase"] == "failed"
        assert evidence["failure"] == "RuntimeError"
        assert evidence["runtime_state_on_failure"] == (
            {
                "exit_code": 125,
                "paused": False,
                "running": False,
                "status": "exited",
            }
            if post_failure == "exited"
            else None
        )


@pytest.mark.parametrize("field", ["schema", "profile", "scenario", "job_ids"])
def test_diagnostic_source_mismatch_never_pauses(
    tmp_path: Path, mocker: MockerFixture, field: str
) -> None:
    state, binding, barrier, command, _runtime, _observed, _inventory = _setup(
        tmp_path, mocker
    )
    value = json.loads(binding.read_text())
    value[field] = "wrong"
    binding.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="source binding differs"):
        fixture.watch_pause(
            tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
        )
    assert not barrier.exists()
    command.assert_not_called()


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
        fixture.watch_pause(
            tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
        )
    command.assert_not_called()
    assert not barrier.exists()


def test_observer_termination_resumes_owned_unit(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, _runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )

    def interrupt_pause(arguments, **_kwargs):
        if arguments[0] == "pause":
            _runtime.state["Paused"] = True
            signal.raise_signal(signal.SIGTERM)
        if arguments[0] == "unpause":
            _runtime.state["Paused"] = False
        return 0, b""

    command.side_effect = interrupt_pause
    with pytest.raises(InterruptedError, match="observer interrupted"):
        fixture.watch_pause(
            tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
        )
    assert not barrier.exists()
    command.assert_any_call(("unpause", observed.container_id))


def test_late_binding_is_resolved_before_candidate_selection(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, _barrier, _command, _runtime, observed, _inventory = _setup(
        tmp_path, mocker
    )
    binding.with_suffix(".ready").unlink()

    def publish_binding(_seconds: float) -> None:
        binding.with_suffix(".ready").touch()

    mocker.patch.object(fixture.time, "sleep", side_effect=publish_binding)
    assert fixture._await_binding(
        json.loads(state.read_text()), binding, fixture.time.monotonic() + 1
    ) == UUID(str(observed.attempt_id))


def test_second_attempt_binding_never_pauses(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, _runtime, _observed, _inventory = _setup(
        tmp_path, mocker
    )
    value = json.loads(binding.read_text())
    value["attempts"][0]["attempt_number"] = 2
    binding.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="first attempt is unavailable"):
        fixture.watch_pause(
            tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
        )
    command.assert_not_called()
    assert not barrier.exists()


def test_vanished_target_never_pauses_unrelated_unit(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    state, binding, barrier, command, runtime, _observed, inventory = _setup(
        tmp_path, mocker
    )
    inventory.unacknowledged.side_effect = None
    inventory.unacknowledged.return_value = (inventory.other_unit,)
    runtime.discover.return_value = ()
    runtime.discover.side_effect = None
    mocker.patch.object(fixture.time, "monotonic", side_effect=[0, 0, 31])
    mocker.patch.object(fixture.time, "sleep")
    with pytest.raises(RuntimeError, match="pause timed out"):
        fixture.watch_pause(
            tmp_path, state, binding, barrier, tmp_path / "pause-state.json"
        )
    command.assert_not_called()
    assert not barrier.exists()


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
    mocker.patch.object(fixture, "_process_children", return_value={43})
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
        fixture,
        "_process_children",
        return_value={43, 44} if fault == "children" else {43},
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


@pytest.mark.parametrize("fault", [None, "overflow", "invalid", "vanished"])
def test_children_include_secondary_threads_with_bounded_metadata(
    tmp_path: Path, mocker: MockerFixture, fault: str | None
) -> None:
    count = 257 if fault == "overflow" else 3
    mocker.patch.object(
        Path, "iterdir", return_value=iter(tmp_path / str(i) for i in range(count))
    )
    values = [b"", b"43", b"43"]
    if fault == "invalid":
        values[1] = b"unknown"
    reads = mocker.patch.object(
        fixture,
        "_proc_read",
        side_effect=values
        if fault != "vanished"
        else [FileNotFoundError(), b"43", b""],
    )
    if fault in {"overflow", "invalid"}:
        with pytest.raises(RuntimeError):
            fixture._process_children(42)
        if fault == "overflow":
            reads.assert_not_called()
    else:
        assert fixture._process_children(42) == {43}
