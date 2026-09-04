from __future__ import annotations

import json
import stat
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid5

import pytest

from markweave.broker import podman_runtime
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    ManagedUnit,
    ManagedUnitState,
    RuntimeIncarnation,
    RuntimeLimits,
    policy_specification_evidence,
)
from markweave.broker.podman_runtime import (
    BoundedCommandRunner,
    PodmanCommandLimits,
    PodmanIsolationRuntime,
    PodmanRuntimeError,
    PodmanRuntimeUnit,
)
from tests.unit.broker.runtime_conformance import assert_lifecycle_conformance

ATTEMPT_ID = UUID("11111111-1111-4111-8111-111111111111")
UNIT_ID = UUID("22222222-2222-4222-8222-222222222222")
PRINCIPAL_ID = UUID("33333333-3333-4333-8333-333333333333")
CONTAINER_ID = "4" * 64
IMAGE_DIGEST = f"sha256:{'5' * 64}"
IMAGE_REPOSITORY = "localhost/markweave-reverse-attempt"
NAME = "markweave-reverse-22222222222242228222222222222222"
INCARNATION_NAMESPACE = UUID("9448db2f-5c64-48eb-a960-d520fac4fb5f")


@pytest.fixture
def policy() -> BrokerPolicy:
    return BrokerPolicy(
        "t70-test",
        IMAGE_DIGEST,
        RuntimeLimits(100_001, 100_000, 268_435_456, 31, 16_777_216, 2_001),
    )


@pytest.fixture
def unit(policy: BrokerPolicy) -> ManagedUnit:
    return ManagedUnit(
        ATTEMPT_ID,
        UNIT_ID,
        AuthenticatedPrincipal(PRINCIPAL_ID),
        1,
        policy.revision,
        policy_specification_evidence(policy),
        ManagedUnitState.CREATE_INTENT,
        1,
    )


class PodmanDouble:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], int | None, frozenset[int]]] = []
        self.exists = False
        self.status = "created"
        self.container_id = CONTAINER_ID
        self.create_arguments: tuple[str, ...] = ()
        self.overrides: dict[str, Any] = {}
        self.events: list[Mapping[str, Any]] = []
        self.create_code = 0
        self.create_output = CONTAINER_ID.encode()

    def __call__(  # noqa: PLR0911 - one bounded response per emulated subcommand
        self,
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        argv = tuple(arguments)
        self.calls.append((argv, max_output_bytes, accepted_exit_codes))
        if argv[0] == "info":
            return 0, (
                b'{"host":{"cgroupVersion":"v2","eventLogger":"journald",'
                b'"security":{"rootless":true,"seccompEnabled":true}}}'
            )
        if argv[0] == "create":
            self.create_arguments = argv
            if self.create_code == 0:
                self.exists = True
            return self.create_code, self.create_output
        if argv[:2] == ("container", "inspect"):
            if not self.exists:
                raise PodmanRuntimeError("Podman command failed")
            return 0, json.dumps([self.inspect()]).encode()
        if argv[0] == "start":
            self.status = "running"
            return 0, (self.container_id + "\n").encode()
        if argv[0] == "kill":
            self.status = "exited"
            return 0, (self.container_id + "\n").encode()
        if argv[:2] == ("container", "exists"):
            return (0 if self.exists else 1), b""
        if argv[0] == "rm":
            self.exists = False
            self.events.append(
                {
                    "ID": self.container_id,
                    "Name": NAME,
                    "Status": "remove",
                    "TimeNano": 123456789,
                }
            )
            return 0, (self.container_id + "\n").encode()
        if argv[0] == "events":
            output = b"".join(
                json.dumps(event, separators=(",", ":")).encode() + b"\n"
                for event in self.events
            )
            return 0, output
        if argv[0] == "ps":
            summaries = [{"Names": [NAME]}] if self.exists else []
            return 0, json.dumps(summaries).encode()
        raise AssertionError(argv)

    def inspect(self) -> dict[str, Any]:
        labels = {}
        iterator = iter(enumerate(self.create_arguments))
        for index, argument in iterator:
            if argument == "--label":
                value = self.create_arguments[index + 1]
                key, label = value.split("=", 1)
                labels[key] = label
        state = {
            "ConmonPid": 0,
            "ExecIDs": [],
            "ExitCode": 137,
            "FinishedAt": "2026-09-04T20:00:00.123456789Z",
            "OOMKilled": False,
            "Paused": False,
            "Pid": 0 if self.status != "running" else 123,
            "Restarting": False,
            "Running": self.status == "running",
            "Status": self.status,
        }
        value: dict[str, Any] = {
            "Config": {
                "CreateCommand": ["/usr/bin/podman", *self.create_arguments],
                "Labels": labels,
            },
            "Id": self.container_id,
            "ImageDigest": IMAGE_DIGEST,
            "ImageName": f"{IMAGE_REPOSITORY}@{IMAGE_DIGEST}",
            "Name": NAME,
            "State": state,
        }
        for path, override in self.overrides.items():
            target = value
            parts = path.split(".")
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = override
        return value


def runtime(command: PodmanDouble) -> PodmanIsolationRuntime:
    return PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=command,
        event_lookback_seconds=600,
    )


def created_runtime(
    command: PodmanDouble, unit: ManagedUnit, policy: BrokerPolicy
) -> PodmanRuntimeUnit:
    return runtime(command).create(unit, policy)


@pytest.mark.unit
def test_create_uses_exact_broker_owned_policy_argv(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()

    result = created_runtime(command, unit, policy)

    create = next(call[0] for call in command.calls if call[0][0] == "create")
    assert result.unit_id == UNIT_ID
    assert create[:14] == (
        "create",
        "--pull=never",
        "--name",
        NAME,
        "--network=none",
        "--read-only",
        "--read-only-tmpfs=false",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--user",
        "1001:0",
        "--cgroups=enabled",
        "--ipc=none",
        "--pid=private",
    )
    assert "--uts=private" in create
    assert create[create.index("--pids-limit") + 1] == "31"
    assert create[create.index("--memory") + 1] == "268435456b"
    assert create[create.index("--memory-swap") + 1] == "268435456b"
    assert create[create.index("--cpu-period") + 1] == "100000"
    assert create[create.index("--cpu-quota") + 1] == "100001"
    assert create[create.index("--timeout") + 1] == "3"
    assert create[create.index("--stop-timeout") + 1] == "0"
    assert create[create.index("--mount") + 1] == (
        "type=tmpfs,destination=/work,tmpfs-mode=0700,tmpfs-size=16777216"
    )
    assert create[create.index("--entrypoint") + 1] == (
        '["python","-m","markweave.reversions.attempt_main"]'
    )
    assert create[-1] == f"{IMAGE_REPOSITORY}@{IMAGE_DIGEST}"
    assert not any(value in create for value in ("--volume", "--device", "--secret"))


@pytest.mark.unit
def test_podman_backend_satisfies_shared_runtime_contract(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    assert_lifecycle_conformance(runtime(PodmanDouble()), unit, policy)


@pytest.mark.unit
def test_create_recovers_exact_lost_response_without_duplicate(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    backend = runtime(command)
    first = backend.create(unit, policy)
    command.create_code = 125
    command.create_output = b""

    second = backend.create(unit, policy)

    assert second == first
    assert [call[0][0] for call in command.calls].count("start") == 1


@pytest.mark.unit
def test_successful_create_requires_exact_returned_container_identity(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    command.create_output = b""

    with pytest.raises(PodmanRuntimeError, match="identity"):
        created_runtime(command, unit, policy)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "override"),
    [
        ("Id", "bad"),
        ("ImageDigest", f"sha256:{'6' * 64}"),
        ("ImageName", "localhost/substituted@" + IMAGE_DIGEST),
        ("Name", "substituted"),
        ("Config.CreateCommand", ["podman", "create", "--privileged"]),
    ],
)
def test_create_rejects_substituted_runtime_specification(
    unit: ManagedUnit, policy: BrokerPolicy, path: str, override: object
) -> None:
    command = PodmanDouble()
    command.overrides[path] = override

    with pytest.raises(PodmanRuntimeError):
        created_runtime(command, unit, policy)


@pytest.mark.unit
def test_create_rejects_caller_owned_model_substitution(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    substituted = BrokerPolicy(
        policy.revision,
        f"sha256:{'9' * 64}",
        policy.limits,
    )

    with pytest.raises(PodmanRuntimeError, match="create contract"):
        runtime(command).create(unit, substituted)

    assert command.calls == []


@pytest.mark.unit
def test_created_crash_point_is_started_then_whole_container_killed(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    runtime_unit = created_runtime(command, unit, policy)
    command.status = "created"
    command.calls.clear()

    runtime(command).hard_terminate(runtime_unit)

    operations = [call[0][0] for call in command.calls]
    assert operations == [
        "info",
        "container",
        "start",
        "container",
        "kill",
        "container",
    ]
    kill = next(call[0] for call in command.calls if call[0][0] == "kill")
    assert kill[1:3] == ("--signal", "KILL")


@pytest.mark.unit
def test_kill_acknowledgement_without_stopped_state_is_insufficient(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    runtime_unit = created_runtime(command, unit, policy)

    def ignored_kill(
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        if arguments[0] == "kill":
            return 125, b""
        return command(
            arguments,
            max_output_bytes=max_output_bytes,
            accepted_exit_codes=accepted_exit_codes,
        )

    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=ignored_kill,
        event_lookback_seconds=600,
    )
    with pytest.raises(PodmanRuntimeError, match="unconfirmed"):
        backend.hard_terminate(runtime_unit)


@pytest.mark.unit
def test_exit_empty_and_removal_require_independent_positive_evidence(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    backend = runtime(command)
    runtime_unit = backend.create(unit, policy)

    with pytest.raises(PodmanRuntimeError, match="exit is unconfirmed"):
        backend.confirm_exit(runtime_unit)
    backend.hard_terminate(runtime_unit)
    backend.hard_terminate(runtime_unit)
    exit_evidence = backend.confirm_exit(runtime_unit)
    empty_evidence = backend.confirm_empty(runtime_unit)
    backend.remove(runtime_unit)
    backend.remove(runtime_unit)
    removal_evidence = backend.confirm_removed(runtime_unit)

    assert len({exit_evidence, empty_evidence, removal_evidence}) == 3
    assert command.calls[-1][0][0] == "events"


@pytest.mark.unit
def test_absence_and_remove_acknowledgement_are_not_removal_proof(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    backend = runtime(command)
    runtime_unit = backend.create(unit, policy)
    command.exists = False

    with pytest.raises(PodmanRuntimeError, match="evidence"):
        backend.confirm_removed(runtime_unit)


@pytest.mark.unit
def test_removed_proof_reconstructs_from_persisted_incarnation(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    backend = runtime(command)
    runtime_unit = backend.create(unit, policy)
    backend.hard_terminate(runtime_unit)
    backend.remove(runtime_unit)

    class StoredUnit:
        unit_id = UNIT_ID
        incarnation = runtime_unit.incarnation

    assert backend.confirm_removed(StoredUnit()).value.startswith("sha256:")


@pytest.mark.unit
@pytest.mark.parametrize(
    "override",
    [
        {"State.Pid": 1},
        {"State.ConmonPid": 1},
        {"State.ExecIDs": ["exec"]},
        {"State.Status": "running"},
    ],
)
def test_empty_proof_rejects_any_reported_container_member(
    unit: ManagedUnit, policy: BrokerPolicy, override: dict[str, object]
) -> None:
    command = PodmanDouble()
    backend = runtime(command)
    runtime_unit = backend.create(unit, policy)
    backend.hard_terminate(runtime_unit)
    command.overrides.update(override)

    with pytest.raises(PodmanRuntimeError, match="not empty"):
        backend.confirm_empty(runtime_unit)


@pytest.mark.unit
def test_discovery_is_bounded_sorted_and_rejects_duplicates(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    backend = runtime(command)
    backend.create(unit, policy)
    assert backend.discover(limit=1)[0].unit_id == UNIT_ID

    original = command.__call__

    def duplicated(
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        if arguments[0] == "ps":
            return 0, json.dumps([{"Names": [NAME]}, {"Names": [NAME]}]).encode()
        return original(
            arguments,
            max_output_bytes=max_output_bytes,
            accepted_exit_codes=accepted_exit_codes,
        )

    duplicate_backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=duplicated,
        event_lookback_seconds=600,
    )
    with pytest.raises(PodmanRuntimeError, match="exceeds"):
        duplicate_backend.discover(limit=1)
    with pytest.raises(PodmanRuntimeError, match="duplicated"):
        duplicate_backend.discover(limit=2)


@pytest.mark.unit
@pytest.mark.parametrize("output", [b"{", b"[]\n{}", b"\xff"])
def test_discovery_rejects_malformed_cli_json(output: bytes) -> None:
    def malformed(*args: object, **kwargs: object) -> tuple[int, bytes]:
        return 0, output

    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=malformed,
        event_lookback_seconds=600,
    )
    with pytest.raises(PodmanRuntimeError):
        backend.discover(limit=1)


@pytest.mark.unit
@pytest.mark.parametrize(
    "information",
    [
        {
            "host": {
                "cgroupVersion": "v1",
                "eventLogger": "journald",
                "security": {"rootless": True, "seccompEnabled": True},
            }
        },
        {
            "host": {
                "cgroupVersion": "v2",
                "eventLogger": "none",
                "security": {"rootless": True, "seccompEnabled": True},
            }
        },
        {
            "host": {
                "cgroupVersion": "v2",
                "eventLogger": "journald",
                "security": {"rootless": False, "seccompEnabled": True},
            }
        },
        {
            "host": {
                "cgroupVersion": "v2",
                "eventLogger": "journald",
                "security": {"rootless": True, "seccompEnabled": False},
            }
        },
    ],
)
def test_runtime_rejects_non_rootless_or_non_durable_environment(
    information: dict[str, object],
) -> None:
    def invalid(*args: object, **kwargs: object) -> tuple[int, bytes]:
        return 0, json.dumps(information).encode()

    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=invalid,
        event_lookback_seconds=600,
    )
    with pytest.raises(PodmanRuntimeError, match="environment"):
        backend.discover(limit=1)


@pytest.mark.unit
def test_command_runner_kills_timed_out_process_group(tmp_path: Path) -> None:
    executable = tmp_path / "blocked"
    executable.write_text(
        "#!/bin/sh\ntrap '' TERM\nsleep 30 &\nwait\n", encoding="utf-8"
    )
    executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    runner = BoundedCommandRunner(executable, PodmanCommandLimits(0.05))

    with pytest.raises(PodmanRuntimeError, match="bounds"):
        runner(("fixed",))


@pytest.mark.unit
def test_command_runner_bounds_output_and_hides_stderr(tmp_path: Path) -> None:
    executable = tmp_path / "output"
    executable.write_text(
        "#!/bin/sh\nprintf secret >&2\nhead -c 2048 /dev/zero\n",
        encoding="utf-8",
    )
    executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    runner = BoundedCommandRunner(executable, PodmanCommandLimits(1, 1024))

    with pytest.raises(PodmanRuntimeError, match="bounds") as raised:
        runner(("fixed",))

    assert "secret" not in str(raised.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"image_repository": "not-pinned"},
        {"run_as_uid": 0},
        {"event_lookback_seconds": 0},
    ],
)
def test_runtime_rejects_unsafe_configuration(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "image_repository": IMAGE_REPOSITORY,
        "run_as_uid": 1001,
        "command": PodmanDouble(),
        "event_lookback_seconds": 600,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        PodmanIsolationRuntime(**values)  # type: ignore[arg-type]


@pytest.mark.unit
def test_runtime_unit_rejects_malformed_identity(policy: BrokerPolicy) -> None:
    incarnation = RuntimeIncarnation(
        uuid5(INCARNATION_NAMESPACE, CONTAINER_ID),
        policy_specification_evidence(policy),
    )
    with pytest.raises(ValueError):
        PodmanRuntimeUnit(UNIT_ID, incarnation, "bad", NAME)
    with pytest.raises(PodmanRuntimeError, match="identity"):
        runtime(PodmanDouble()).remove(cast(Any, object()))


@pytest.mark.unit
@pytest.mark.parametrize(
    "limits",
    [
        (0, 1024),
        (float("inf"), 1024),
        (1, 1),
        (1, 128 * 1024 + 1),
    ],
)
def test_command_limits_reject_invalid_ceilings(limits: tuple[float, int]) -> None:
    with pytest.raises(ValueError):
        PodmanCommandLimits(*limits)


@pytest.mark.unit
def test_command_runner_rejects_invalid_contract_and_failed_exec(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError):
        BoundedCommandRunner(Path("relative"), PodmanCommandLimits(1))
    missing = BoundedCommandRunner(tmp_path / "missing", PodmanCommandLimits(1))
    with pytest.raises(PodmanRuntimeError, match="failed"):
        missing(("fixed",))
    failed = BoundedCommandRunner(Path("/bin/false"), PodmanCommandLimits(1))
    with pytest.raises(PodmanRuntimeError, match="failed"):
        failed(("fixed",))
    with pytest.raises(PodmanRuntimeError, match="contract"):
        failed(())
    with pytest.raises(PodmanRuntimeError, match="contract"):
        failed(("fixed",), max_output_bytes=128 * 1024 + 1)


@pytest.mark.unit
def test_create_rejects_non_ascii_identity_and_unknown_state(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    command.create_output = b"\xff"
    with pytest.raises(PodmanRuntimeError, match="identity"):
        created_runtime(command, unit, policy)

    command = PodmanDouble()
    command.status = "unknown"
    with pytest.raises(PodmanRuntimeError, match="state"):
        created_runtime(command, unit, policy)


@pytest.mark.unit
def test_removal_rejects_live_unit_and_duplicate_event(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    backend = runtime(command)
    runtime_unit = backend.create(unit, policy)
    with pytest.raises(PodmanRuntimeError, match="unconfirmed"):
        backend.confirm_removed(runtime_unit)
    backend.hard_terminate(runtime_unit)
    backend.remove(runtime_unit)
    command.events.append(dict(command.events[0]))
    with pytest.raises(PodmanRuntimeError, match="evidence"):
        backend.confirm_removed(runtime_unit)


@pytest.mark.unit
@pytest.mark.parametrize(
    "summary",
    [{}, {"Names": []}, {"Names": [NAME, NAME]}, {"Names": [1]}, 1],
)
def test_discovery_rejects_malformed_summary(summary: object) -> None:
    command = PodmanDouble()

    def malformed(
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        if arguments[0] == "ps":
            return 0, json.dumps([summary]).encode()
        return command(
            arguments,
            max_output_bytes=max_output_bytes,
            accepted_exit_codes=accepted_exit_codes,
        )

    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=malformed,
        event_lookback_seconds=600,
    )
    with pytest.raises(PodmanRuntimeError):
        backend.discover(limit=1)
    with pytest.raises(PodmanRuntimeError, match="limit"):
        backend.discover(limit=0)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("function", "value", "key"),
    [
        (podman_runtime._mapping, {}, "missing"),
        (podman_runtime._string, {"field": 1}, "field"),
        (podman_runtime._boolean, {"field": 1}, "field"),
        (podman_runtime._integer, {"field": True}, "field"),
        (podman_runtime._timestamp, {"field": "not-time"}, "field"),
        (podman_runtime._label, {"field": 1}, "field"),
        (podman_runtime._positive_integer_label, {"field": "0"}, "field"),
    ],
)
def test_podman_evidence_field_parsers_fail_closed(
    function: Callable[[Mapping[str, Any], str], object],
    value: dict[str, object],
    key: str,
) -> None:
    with pytest.raises(PodmanRuntimeError):
        function(value, key)


@pytest.mark.unit
@pytest.mark.parametrize(
    "output",
    [b"not-json\n", b"[]\n", b"\xff"],
)
def test_event_parser_rejects_noncanonical_json_lines(output: bytes) -> None:
    with pytest.raises(PodmanRuntimeError):
        podman_runtime._json_lines(output)


@pytest.mark.unit
def test_event_time_rejects_missing_or_nonpositive_value() -> None:
    with pytest.raises(PodmanRuntimeError):
        podman_runtime._event_time({})
    with pytest.raises(PodmanRuntimeError):
        podman_runtime._event_time({"TimeNano": 0})
