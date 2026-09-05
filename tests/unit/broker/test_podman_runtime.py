from __future__ import annotations

import json
import stat
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid5

import pytest
from pytest_mock import MockerFixture

from markweave.broker import podman_runtime
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    EvidenceDigest,
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
    SystemdCgroupRemover,
)
from tests.unit.broker.runtime_conformance import assert_lifecycle_conformance

ATTEMPT_ID = UUID("11111111-1111-4111-8111-111111111111")
UNIT_ID = UUID("22222222-2222-4222-8222-222222222222")
PRINCIPAL_ID = UUID("33333333-3333-4333-8333-333333333333")
CONTAINER_ID = "4" * 64
IMAGE_DIGEST = f"sha256:{'5' * 64}"
IMAGE_REPOSITORY = "localhost/markweave-reverse-attempt"
NAME = "markweave-reverse-22222222222242228222222222222222"
HOOKS = Path("/var/empty/markweave-hooks")
CAPABILITIES = (
    "CAP_CHOWN",
    "CAP_DAC_OVERRIDE",
    "CAP_FOWNER",
    "CAP_FSETID",
    "CAP_KILL",
    "CAP_NET_BIND_SERVICE",
    "CAP_SETFCAP",
    "CAP_SETGID",
    "CAP_SETPCAP",
    "CAP_SETUID",
    "CAP_SYS_CHROOT",
)
INCARNATION_NAMESPACE = UUID("9448db2f-5c64-48eb-a960-d520fac4fb5f")
CGROUP_ROOT = Path("/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service")
CGROUP_RUNTIME_PATH = (
    "/user.slice/user-1000.slice/user@1000.service/"
    f"markweavet70{UNIT_ID.hex}.slice/libpod-{CONTAINER_ID}.scope"
)


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
        self.full_create_arguments: tuple[str, ...] = ()
        self.overrides: dict[str, Any] = {}
        self.removed_paths: set[str] = set()
        self.create_code = 0
        self.create_output = CONTAINER_ID.encode()
        self.rm_code = 0
        self.rm_removes = True
        self.cgroup_removals: list[Path] = []

    def remove_cgroup(self, path: Path) -> None:
        self.cgroup_removals.append(path)

    def __call__(  # noqa: PLR0911 - one bounded response per emulated subcommand
        self,
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        raw = tuple(arguments)
        assert raw[:3] == ("--remote=false", "--hooks-dir", str(HOOKS))
        argv = raw[3:]
        self.calls.append((argv, max_output_bytes, accepted_exit_codes))
        if argv[0] == "info":
            return 0, (
                json.dumps(
                    {
                        "host": {
                            "cgroupManager": "systemd",
                            "cgroupVersion": "v2",
                            "serviceIsRemote": False,
                            "security": {
                                "capabilities": ",".join(CAPABILITIES),
                                "rootless": True,
                                "seccompEnabled": True,
                            },
                        }
                    }
                ).encode()
            )
        if argv[0] == "create":
            self.create_arguments = argv
            self.full_create_arguments = raw
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
            if self.rm_removes:
                self.exists = False
            return self.rm_code, (self.container_id + "\n").encode()
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
            "CgroupPath": ("" if self.status == "created" else CGROUP_RUNTIME_PATH),
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
                "Cmd": None,
                "CreateCommand": ["/usr/bin/podman", *self.full_create_arguments],
                "Entrypoint": list(podman_runtime._FIXED_ENTRYPOINT),
                "Env": [*podman_runtime._FIXED_ENVIRONMENT, f"HOSTNAME={NAME}"],
                "Labels": labels,
                "StopTimeout": 0,
                "Timeout": 3,
                "User": "1001:0",
                "WorkingDir": "/work",
            },
            "HostConfig": {
                "AutoRemove": False,
                "Binds": [],
                "CapAdd": [],
                "CapDrop": list(CAPABILITIES),
                "CgroupParent": f"markweavet70{UNIT_ID.hex}.slice",
                "Cgroups": "default",
                "CpuPeriod": 100_000,
                "CpuQuota": 100_001,
                "Devices": [],
                "Dns": [],
                "DnsOptions": [],
                "DnsSearch": [],
                "ExtraHosts": [],
                "GroupAdd": [],
                "IpcMode": "none",
                "LogConfig": {
                    "Config": None,
                    "Path": "",
                    "Size": "0B",
                    "Tag": "",
                    "Type": "none",
                },
                "Memory": 268_435_456,
                "MemorySwap": 268_435_456,
                "NetworkMode": "none",
                "PidMode": "private",
                "PidsLimit": 31,
                "PortBindings": {},
                "Privileged": False,
                "PublishAllPorts": False,
                "ReadonlyRootfs": True,
                "RestartPolicy": {"MaximumRetryCount": 0, "Name": "no"},
                "SecurityOpt": ["no-new-privileges"],
                "Tmpfs": {
                    "/work": "mode=0770,size=16777216,rw,rprivate,nosuid,nodev,tmpcopyup"
                },
                "UTSMode": "private",
                "VolumesFrom": None,
            },
            "Id": self.container_id,
            "ImageDigest": IMAGE_DIGEST,
            "ImageName": f"{IMAGE_REPOSITORY}@{IMAGE_DIGEST}",
            "Name": NAME,
            "Mounts": [],
            "ExecIDs": [],
            "State": state,
        }
        for path, override in self.overrides.items():
            target = value
            parts = path.split(".")
            for part in parts[:-1]:
                target = target[part]
            target[parts[-1]] = override
        for path in self.removed_paths:
            target = value
            parts = path.split(".")
            for part in parts[:-1]:
                target = target[part]
            del target[parts[-1]]
        return value


def runtime(command: PodmanDouble) -> PodmanIsolationRuntime:
    return PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=command,
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=command.remove_cgroup,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_create=lambda path: None,
        cgroup_read=lambda path: (
            b"populated 1\nfrozen 0\n"
            if command.status == "running"
            else b"populated 0\nfrozen 0\n"
        ),
        process_cgroup_read=lambda pid: f"0::{CGROUP_RUNTIME_PATH}\n".encode(),
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
    assert create[:18] == (
        "create",
        "--pull=never",
        "--name",
        NAME,
        "--hostname",
        NAME,
        "--network=none",
        "--read-only",
        "--read-only-tmpfs=false",
        "--cap-drop=all",
        "--security-opt=no-new-privileges",
        "--user",
        "1001:0",
        "--cgroups=enabled",
        "--cgroup-parent",
        f"markweavet70{UNIT_ID.hex}.slice",
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
        "type=tmpfs,destination=/work,tmpfs-mode=0770,tmpfs-size=16777216"
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
def test_failed_create_without_container_removes_only_the_empty_cgroup(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    command.create_code = 125
    command.create_output = b""

    with pytest.raises(PodmanRuntimeError, match="command failed"):
        runtime(command).create(unit, policy)

    assert command.exists is False
    assert not any(call[0][0] == "rm" for call in command.calls)
    assert ("container", "exists", NAME) in [call[0] for call in command.calls]
    assert command.cgroup_removals == [CGROUP_ROOT / f"markweavet70{UNIT_ID.hex}.slice"]


@pytest.mark.unit
def test_failed_create_requires_both_name_and_label_absence_before_cgroup_cleanup(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    command.create_code = 125
    command.create_output = b""
    original = command.__call__

    def substituted_discovery(
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        if arguments[3] == "ps":
            return 0, json.dumps([{"Names": ["substituted"]}]).encode()
        return original(
            arguments,
            max_output_bytes=max_output_bytes,
            accepted_exit_codes=accepted_exit_codes,
        )

    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=substituted_discovery,
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=command.remove_cgroup,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_create=lambda path: None,
    )

    with pytest.raises(PodmanRuntimeError, match="cleanup"):
        backend.create(unit, policy)

    assert command.cgroup_removals == []


@pytest.mark.unit
def test_successful_create_requires_exact_returned_container_identity(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    command.create_output = b""

    with pytest.raises(PodmanRuntimeError, match="cleanup"):
        created_runtime(command, unit, policy)
    assert command.exists is True
    assert not any(call[0][0] == "rm" for call in command.calls)
    assert command.cgroup_removals == []


@pytest.mark.unit
def test_failed_create_cleanup_uses_only_the_authoritative_returned_identity(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    returned_id = "6" * 64
    command.create_output = returned_id.encode()
    cleanup_calls: list[tuple[str, ...]] = []
    original = command.__call__

    def divergent_boundary(
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        argv = tuple(arguments)[3:]
        if argv[0] == "rm":
            cleanup_calls.append(argv)
            return 0, (returned_id + "\n").encode()
        if argv[:2] == ("container", "exists") and argv[2] == returned_id:
            cleanup_calls.append(argv)
            return 1, b""
        return original(
            arguments,
            max_output_bytes=max_output_bytes,
            accepted_exit_codes=accepted_exit_codes,
        )

    removed_cgroups: list[Path] = []
    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=divergent_boundary,
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=removed_cgroups.append,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_create=lambda path: None,
        cgroup_read=lambda path: b"populated 1\nfrozen 0\n",
        process_cgroup_read=lambda pid: f"0::{CGROUP_RUNTIME_PATH}\n".encode(),
    )

    with pytest.raises(PodmanRuntimeError, match="cleanup"):
        backend.create(unit, policy)

    assert cleanup_calls == [
        ("rm", "--force", returned_id),
        ("container", "exists", returned_id),
    ]
    assert command.exists is True
    assert all(call != ("rm", "--force", CONTAINER_ID) for call in cleanup_calls)
    assert removed_cgroups == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("path", "override"),
    [
        ("Id", "bad"),
        ("ImageDigest", f"sha256:{'6' * 64}"),
        ("ImageName", "localhost/substituted@" + IMAGE_DIGEST),
        ("Name", "substituted"),
        ("Config.CreateCommand", ["podman", "create", "--privileged"]),
        ("Config.Entrypoint", "python -m substituted"),
        ("Config.Env", [f"HOSTNAME={NAME}", "INJECTED=1"]),
        ("HostConfig.Binds", ["/srv/injected:/work"]),
        ("HostConfig.SecurityOpt", []),
        ("State.CgroupPath", "/wrong/cgroup.scope"),
    ],
)
def test_create_rejects_substituted_runtime_specification(
    unit: ManagedUnit, policy: BrokerPolicy, path: str, override: object
) -> None:
    command = PodmanDouble()
    command.overrides[path] = override

    with pytest.raises(PodmanRuntimeError):
        created_runtime(command, unit, policy)
    assert command.exists is False
    assert any(call[0][:2] == ("rm", "--force") for call in command.calls)
    assert command.cgroup_removals == [CGROUP_ROOT / f"markweavet70{UNIT_ID.hex}.slice"]


@pytest.mark.unit
def test_create_accepts_only_exact_legacy_podman_entrypoint_projection(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    command.overrides["Config.Entrypoint"] = " ".join(podman_runtime._FIXED_ENTRYPOINT)

    created = runtime(command).create(unit, policy)

    assert created.container_id == CONTAINER_ID
    assert command.status == "running"


@pytest.mark.unit
def test_failed_create_cleanup_does_not_remove_cgroup_while_container_exists(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    command.overrides["Config.Entrypoint"] = "substituted"
    original = command.__call__
    removed_cgroups: list[Path] = []

    def failing_remove(
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        if arguments[3] == "rm":
            raise KeyboardInterrupt
        return original(
            arguments,
            max_output_bytes=max_output_bytes,
            accepted_exit_codes=accepted_exit_codes,
        )

    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=failing_remove,
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=removed_cgroups.append,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_create=lambda path: None,
        cgroup_read=lambda path: b"populated 1\nfrozen 0\n",
        process_cgroup_read=lambda pid: f"0::{CGROUP_RUNTIME_PATH}\n".encode(),
    )

    with pytest.raises(PodmanRuntimeError, match="cleanup"):
        backend.create(unit, policy)

    assert removed_cgroups == []


@pytest.mark.unit
@pytest.mark.parametrize("rm_code", [0, 1])
def test_failed_create_cleanup_rejects_rm_that_leaves_container_present(
    unit: ManagedUnit, policy: BrokerPolicy, rm_code: int
) -> None:
    command = PodmanDouble()
    command.overrides["Config.Entrypoint"] = "substituted"
    command.rm_code = rm_code
    command.rm_removes = False

    with pytest.raises(PodmanRuntimeError, match="cleanup"):
        runtime(command).create(unit, policy)

    assert command.exists is True
    assert command.cgroup_removals == []
    assert any(
        call[0] == ("container", "exists", CONTAINER_ID) for call in command.calls
    )


@pytest.mark.unit
@pytest.mark.parametrize("interruption", [KeyboardInterrupt(), SystemExit(7)])
def test_failed_create_cleanup_re_raises_base_exception_after_confirmed_cleanup(
    unit: ManagedUnit, policy: BrokerPolicy, interruption: BaseException
) -> None:
    command = PodmanDouble()
    original = command.__call__
    interrupted = False

    def interrupted_boundary(
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        nonlocal interrupted
        argv = tuple(arguments)[3:]
        if argv[:2] == ("container", "inspect") and not interrupted:
            interrupted = True
            raise interruption
        return original(
            arguments,
            max_output_bytes=max_output_bytes,
            accepted_exit_codes=accepted_exit_codes,
        )

    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=interrupted_boundary,
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=command.remove_cgroup,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_create=lambda path: None,
    )

    with pytest.raises(type(interruption)):
        backend.create(unit, policy)

    assert command.exists is False
    assert command.cgroup_removals == [CGROUP_ROOT / f"markweavet70{UNIT_ID.hex}.slice"]


@pytest.mark.unit
def test_unconfirmed_cleanup_masks_base_exception_with_content_free_error(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    command.rm_removes = False
    original = command.__call__

    def interrupted_boundary(
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        if tuple(arguments)[3:5] == ("container", "inspect"):
            raise KeyboardInterrupt
        return original(
            arguments,
            max_output_bytes=max_output_bytes,
            accepted_exit_codes=accepted_exit_codes,
        )

    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=interrupted_boundary,
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=command.remove_cgroup,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_create=lambda path: None,
    )

    with pytest.raises(PodmanRuntimeError, match="cleanup") as raised:
        backend.create(unit, policy)

    assert str(raised.value) == "Podman failed creation cleanup is unconfirmed"
    assert command.exists is True
    assert command.cgroup_removals == []


@pytest.mark.unit
def test_failed_create_cleanup_rejects_unconfirmed_cgroup_removal(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    command.overrides["Config.Entrypoint"] = "substituted"

    def unavailable(path: Path) -> None:
        del path
        raise OSError("secret")

    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=command,
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=unavailable,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_create=lambda path: None,
        cgroup_read=lambda path: b"populated 1\nfrozen 0\n",
        process_cgroup_read=lambda pid: f"0::{CGROUP_RUNTIME_PATH}\n".encode(),
    )

    with pytest.raises(PodmanRuntimeError, match="cleanup") as raised:
        backend.create(unit, policy)
    assert "secret" not in str(raised.value)


@pytest.mark.unit
def test_recovered_container_validation_failure_is_not_removed(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    backend = runtime(command)
    backend.create(unit, policy)
    command.create_code = 125
    command.create_output = b""
    command.overrides["Config.Entrypoint"] = "substituted"

    with pytest.raises(PodmanRuntimeError, match="realized"):
        backend.create(unit, policy)

    assert command.exists is True
    assert command.cgroup_removals == []


@pytest.mark.unit
def test_failed_create_without_exact_container_id_reports_cleanup_failure(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    command.create_output = b"\xff"
    command.overrides["Id"] = "invalid"

    with pytest.raises(PodmanRuntimeError, match="cleanup"):
        runtime(command).create(unit, policy)

    assert command.exists is True
    assert command.cgroup_removals == []


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
        if arguments[3] == "kill":
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
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=lambda path: None,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_read=lambda path: b"populated 1\nfrozen 0\n",
        process_cgroup_read=lambda pid: f"0::{CGROUP_RUNTIME_PATH}\n".encode(),
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
    removal_evidence = backend.confirm_removed(runtime_unit, empty_evidence)

    assert len({exit_evidence, empty_evidence, removal_evidence}) == 3
    assert command.calls[-1][0][0] == "ps"


@pytest.mark.unit
def test_absence_and_remove_acknowledgement_are_not_removal_proof(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    backend = runtime(command)
    runtime_unit = backend.create(unit, policy)
    command.exists = False

    with pytest.raises(PodmanRuntimeError, match="evidence"):
        backend.confirm_removed(runtime_unit, cast(Any, object()))


@pytest.mark.unit
def test_removed_proof_reconstructs_from_persisted_incarnation(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    backend = runtime(command)
    runtime_unit = backend.create(unit, policy)
    backend.hard_terminate(runtime_unit)
    empty_evidence = backend.confirm_empty(runtime_unit)
    backend.remove(runtime_unit)

    class StoredUnit:
        unit_id = UNIT_ID
        incarnation = runtime_unit.incarnation

    assert backend.confirm_removed(StoredUnit(), empty_evidence).value.startswith(
        "sha256:"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "override",
    [
        {"State.Pid": 1},
        {"ExecIDs": ["exec"]},
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
def test_empty_proof_requires_top_level_exec_ids_and_positive_cgroup_evidence(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    runtime_unit = created_runtime(command, unit, policy)
    command.status = "exited"
    command.removed_paths.add("ExecIDs")
    command.overrides["State.ExecIDs"] = []
    with pytest.raises(PodmanRuntimeError, match="not empty"):
        runtime(command).confirm_empty(runtime_unit)

    command.removed_paths.clear()
    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=command,
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=lambda path: None,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_read=lambda path: b"populated 1\nfrozen 0\n",
    )
    with pytest.raises(PodmanRuntimeError, match="cgroup"):
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
        if arguments[3] == "ps":
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
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=lambda path: None,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_read=lambda path: b"populated 1\nfrozen 0\n",
        process_cgroup_read=lambda pid: f"0::{CGROUP_RUNTIME_PATH}\n".encode(),
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
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=lambda path: None,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
    )
    with pytest.raises(PodmanRuntimeError):
        backend.discover(limit=1)


@pytest.mark.unit
@pytest.mark.parametrize(
    "information",
    [
        {
            "host": {
                "cgroupManager": "cgroupfs",
                "cgroupVersion": "v2",
                "serviceIsRemote": False,
                "security": {
                    "capabilities": ",".join(CAPABILITIES),
                    "rootless": True,
                    "seccompEnabled": True,
                },
            }
        },
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
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=lambda path: None,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
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
    runner = BoundedCommandRunner(
        executable, PodmanCommandLimits(0.05), environment={"PATH": "/usr/bin:/bin"}
    )

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
    runner = BoundedCommandRunner(
        executable,
        PodmanCommandLimits(1, 1024),
        environment={"PATH": "/usr/bin:/bin"},
    )

    with pytest.raises(PodmanRuntimeError, match="bounds") as raised:
        runner(("fixed",))

    assert "secret" not in str(raised.value)


@pytest.mark.unit
def test_command_runner_is_hermetic_and_reaps_after_unexpected_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    executable = tmp_path / "environment"
    executable.write_text(
        "#!/bin/sh\nprintf '%s' \"${CONTAINER_HOST-unset}\"\nsleep 30\n",
        encoding="utf-8",
    )
    executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    monkeypatch.setenv("CONTAINER_HOST", "tcp://attacker.invalid")
    _, environment_output = BoundedCommandRunner(
        Path("/usr/bin/env"),
        PodmanCommandLimits(1),
        environment={"PATH": "/usr/bin:/bin"},
    )(("--null",))
    assert b"CONTAINER_HOST" not in environment_output
    original_popen = podman_runtime.subprocess.Popen
    spawned: list[Any] = []

    def capture(*args: Any, **kwargs: Any) -> Any:
        process = original_popen(*args, **kwargs)
        spawned.append(process)
        return process

    mocker.patch.object(podman_runtime.subprocess, "Popen", side_effect=capture)
    mocker.patch.object(
        podman_runtime.selectors.EpollSelector,
        "select",
        side_effect=OSError("secret"),
    )
    runner = BoundedCommandRunner(
        executable,
        PodmanCommandLimits(1),
        environment={"PATH": "/usr/bin:/bin"},
    )

    with pytest.raises(PodmanRuntimeError, match="failed") as raised:
        runner(("fixed",))

    assert "secret" not in str(raised.value)
    assert spawned and spawned[0].poll() is not None


@pytest.mark.unit
def test_command_runner_rejects_remote_authority_environment() -> None:
    with pytest.raises(ValueError):
        BoundedCommandRunner(
            Path("/bin/true"),
            PodmanCommandLimits(1),
            environment={"CONTAINER_HOST": "tcp://attacker.invalid"},
        )


@pytest.mark.unit
def test_hooks_directory_must_be_empty_owner_only_and_not_a_symlink(
    tmp_path: Path,
) -> None:
    hooks = tmp_path / "hooks"
    hooks.mkdir(mode=0o700)
    podman_runtime._validate_hooks_directory(hooks)
    (hooks / "injected.json").write_text("{}", encoding="ascii")
    with pytest.raises(PodmanRuntimeError, match="hooks"):
        podman_runtime._validate_hooks_directory(hooks)
    (hooks / "injected.json").unlink()
    hooks.chmod(0o755)
    with pytest.raises(PodmanRuntimeError, match="hooks"):
        podman_runtime._validate_hooks_directory(hooks)
    link = tmp_path / "link"
    link.symlink_to(hooks, target_is_directory=True)
    with pytest.raises(PodmanRuntimeError, match="hooks"):
        podman_runtime._validate_hooks_directory(link)


@pytest.mark.unit
def test_cgroup_root_and_live_process_binding_fail_closed(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    with pytest.raises(PodmanRuntimeError, match="root"):
        podman_runtime._validate_cgroup_root(Path("/sys/fs/cgroup"))

    command = PodmanDouble()
    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=command,
        cgroup_root=Path("/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service"),
        hooks_directory=HOOKS,
        cgroup_remove=lambda path: None,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_read=lambda path: b"populated 1\nfrozen 0\n",
        process_cgroup_read=lambda pid: b"0::/substituted.scope\n",
        cgroup_create=lambda path: None,
    )
    with pytest.raises(PodmanRuntimeError, match="binding"):
        backend.create(unit, policy)


@pytest.mark.unit
def test_live_process_accepts_exact_crun_systemd_container_subgroup(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    requested_paths: list[Path] = []
    backend = PodmanIsolationRuntime(
        image_repository=IMAGE_REPOSITORY,
        run_as_uid=1001,
        command=command,
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=command.remove_cgroup,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
        cgroup_create=lambda path: None,
        cgroup_read=lambda path: (
            requested_paths.append(path) or b"populated 1\nfrozen 0\n"
        ),
        process_cgroup_read=lambda pid: (
            f"0::{CGROUP_RUNTIME_PATH}/container\n".encode()
        ),
    )

    created = backend.create(unit, policy)

    assert created.container_id == CONTAINER_ID
    expected = CGROUP_ROOT / f"markweavet70{UNIT_ID.hex}.slice"
    assert requested_paths
    assert set(requested_paths) == {expected}


@pytest.mark.unit
@pytest.mark.parametrize(
    "evidence",
    [
        f"0::{CGROUP_RUNTIME_PATH}/container/child\n".encode(),
        f"0::{CGROUP_RUNTIME_PATH}/other\n".encode(),
        f"1::{CGROUP_RUNTIME_PATH}/container\n".encode(),
        f"0::{CGROUP_RUNTIME_PATH}/container".encode(),
        f"0::{CGROUP_RUNTIME_PATH}//container\n".encode(),
        f"0::{CGROUP_RUNTIME_PATH}/container\n0::/other\n".encode(),
    ],
)
def test_process_cgroup_compatibility_rejects_any_other_descendant_or_format(
    evidence: bytes,
) -> None:
    assert not podman_runtime._matches_process_cgroup(evidence, CGROUP_RUNTIME_PATH)


@pytest.mark.unit
@pytest.mark.parametrize(
    "evidence",
    [
        f"0::{CGROUP_RUNTIME_PATH}\n".encode(),
        f"0::{CGROUP_RUNTIME_PATH}/container\n".encode(),
    ],
)
def test_process_cgroup_compatibility_accepts_only_two_exact_forms(
    evidence: bytes,
) -> None:
    assert podman_runtime._matches_process_cgroup(evidence, CGROUP_RUNTIME_PATH)


@pytest.mark.unit
def test_exact_owned_cgroup_root_validation_contract(mocker: MockerFixture) -> None:
    expected = Path("/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service")
    metadata = mocker.Mock(st_mode=stat.S_IFDIR | 0o700, st_uid=1000)
    mocker.patch.object(podman_runtime.os, "geteuid", return_value=1000)
    mocker.patch.object(
        Path, "resolve", autospec=True, side_effect=lambda path, strict: path
    )
    mocker.patch.object(Path, "stat", autospec=True, return_value=metadata)

    podman_runtime._validate_cgroup_root(expected)


@pytest.mark.unit
def test_systemd_cgroup_remover_requires_exact_identity_and_disappearance(
    tmp_path: Path,
) -> None:
    unit_name = f"markweavet70{UNIT_ID.hex}.slice"
    path = tmp_path / unit_name
    path.mkdir()
    calls: list[tuple[Sequence[str], int | None]] = []

    def remove(
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        del accepted_exit_codes
        calls.append((arguments, max_output_bytes))
        if arguments[1] == "stop":
            path.rmdir()
            return 0, b""
        return (
            0,
            b"ControlGroup=\nLoadState=loaded\nActiveState=inactive\nSubState=dead\n",
        )

    remover = SystemdCgroupRemover(remove)
    remover(path)
    assert calls == [
        (("--user", "stop", unit_name), 0),
        (
            (
                "--user",
                "show",
                unit_name,
                "--property=LoadState",
                "--property=ActiveState",
                "--property=SubState",
                "--property=ControlGroup",
            ),
            512,
        ),
    ]

    with pytest.raises(PodmanRuntimeError, match="identity"):
        remover(tmp_path / "markweavet70.slice")

    path.mkdir()
    retained = SystemdCgroupRemover(
        lambda *args, **kwargs: (
            0,
            b"ControlGroup=/live\nLoadState=loaded\nActiveState=active\nSubState=running\n",
        )
    )
    with pytest.raises(PodmanRuntimeError, match="unconfirmed"):
        retained(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "path", [object(), Path("relative.slice"), Path("/var/empty/wrong.slice")]
)
def test_systemd_cgroup_remover_rejects_every_invalid_identity(path: object) -> None:
    with pytest.raises(PodmanRuntimeError, match="identity"):
        SystemdCgroupRemover(lambda *args, **kwargs: (0, b""))(cast(Any, path))


@pytest.mark.unit
def test_systemd_cgroup_remover_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="configuration"):
        SystemdCgroupRemover(cast(Any, None))


@pytest.mark.unit
def test_systemd_cgroup_remover_removes_inactive_empty_precreated_leaf(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = tmp_path / f"markweavet70{UNIT_ID.hex}.slice"
    path.mkdir()
    mocker.patch.object(
        podman_runtime,
        "_read_cgroup_events",
        return_value=b"populated 0\nfrozen 0\n",
    )

    def inactive(*args: object, **kwargs: object) -> tuple[int, bytes]:
        del args, kwargs
        return (
            0,
            b"ControlGroup=\nLoadState=loaded\nActiveState=inactive\nSubState=dead\n",
        )

    SystemdCgroupRemover(inactive)(path)

    assert not path.exists()

    path.mkdir()
    mocker.patch.object(
        podman_runtime,
        "_read_cgroup_events",
        return_value=b"populated 1\nfrozen 0\n",
    )
    with pytest.raises(PodmanRuntimeError, match="unconfirmed"):
        SystemdCgroupRemover(inactive)(path)

    mocker.patch.object(
        podman_runtime,
        "_read_cgroup_events",
        return_value=b"populated 0\nfrozen 0\n",
    )
    mocked_rmdir = mocker.patch.object(Path, "rmdir", return_value=None)
    with pytest.raises(PodmanRuntimeError, match="unconfirmed"):
        SystemdCgroupRemover(inactive)(path)
    mocked_rmdir.assert_called_once()


@pytest.mark.unit
def test_systemd_cgroup_remover_hides_filesystem_failures(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = tmp_path / f"markweavet70{UNIT_ID.hex}.slice"
    path.mkdir()
    mocker.patch.object(Path, "stat", side_effect=PermissionError("secret"))

    def inactive(*args: object, **kwargs: object) -> tuple[int, bytes]:
        del args, kwargs
        return (
            0,
            b"ControlGroup=\nLoadState=loaded\nActiveState=inactive\nSubState=dead\n",
        )

    with pytest.raises(PodmanRuntimeError, match="cleanup failed") as raised:
        SystemdCgroupRemover(inactive)(path)
    assert "secret" not in str(raised.value)


@pytest.mark.unit
def test_systemd_cgroup_remover_hides_rmdir_failure(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    path = tmp_path / f"markweavet70{UNIT_ID.hex}.slice"
    path.mkdir()
    mocker.patch.object(
        podman_runtime,
        "_read_cgroup_events",
        return_value=b"populated 0\nfrozen 0\n",
    )
    mocker.patch.object(Path, "rmdir", side_effect=PermissionError("secret"))

    def inactive(*args: object, **kwargs: object) -> tuple[int, bytes]:
        del args, kwargs
        return (
            0,
            b"ControlGroup=\nLoadState=loaded\nActiveState=inactive\nSubState=dead\n",
        )

    with pytest.raises(PodmanRuntimeError, match="cleanup failed") as raised:
        SystemdCgroupRemover(inactive)(path)
    assert "secret" not in str(raised.value)


@pytest.mark.unit
@pytest.mark.parametrize("pid", [cast(Any, "1"), 0])
def test_process_cgroup_reader_rejects_invalid_pid(pid: int) -> None:
    with pytest.raises(PodmanRuntimeError, match="binding"):
        podman_runtime._read_process_cgroup(pid)


@pytest.mark.unit
def test_cgroup_and_entrypoint_canonicalizers_reject_substitution() -> None:
    with pytest.raises(PodmanRuntimeError, match="identity"):
        podman_runtime._cgroup_parent("substituted")
    assert (
        podman_runtime._matches_entrypoint(tuple(podman_runtime._FIXED_ENTRYPOINT))
        is False
    )


@pytest.mark.unit
def test_cgroup_event_reader_is_bounded_and_requires_available_evidence(
    tmp_path: Path,
) -> None:
    cgroup = tmp_path / "unit.slice"
    cgroup.mkdir()
    events = cgroup / "cgroup.events"
    events.write_bytes(b"populated 0\nfrozen 0\n")
    assert podman_runtime._read_cgroup_events(cgroup) == b"populated 0\nfrozen 0\n"

    events.write_bytes(b"x" * (podman_runtime._CGROUP_EVENTS_MAX_BYTES + 1))
    with pytest.raises(PodmanRuntimeError, match="bound"):
        podman_runtime._read_cgroup_events(cgroup)

    events.unlink()
    with pytest.raises(PodmanRuntimeError, match="unavailable"):
        podman_runtime._read_cgroup_events(cgroup)


@pytest.mark.unit
def test_cgroup_preparation_accepts_an_existing_owned_leaf(tmp_path: Path) -> None:
    cgroup = tmp_path / "unit.slice"
    cgroup.mkdir()
    (cgroup / "cgroup.events").write_bytes(b"populated 0\nfrozen 0\n")

    podman_runtime._create_cgroup(cgroup)

    missing_events = tmp_path / "missing-events.slice"
    with pytest.raises(PodmanRuntimeError, match="preparation"):
        podman_runtime._create_cgroup(missing_events)


@pytest.mark.unit
@pytest.mark.parametrize(
    "evidence",
    [
        b"",
        b"x" * 513,
        b"\xff",
        b"ControlGroup=\nLoadState=loaded\nActiveState=inactive\nSubState=dead",
        b"Unknown=value\n",
        b"ActiveState=inactive\nActiveState=inactive\n",
        b"ControlGroup=\nLoadState=loaded\nActiveState=inactive\n",
    ],
)
def test_systemd_manager_evidence_is_bounded_and_exact(evidence: bytes) -> None:
    with pytest.raises(PodmanRuntimeError, match="systemd evidence"):
        podman_runtime._parse_systemd_properties(evidence)


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs",
    [
        {"image_repository": "not-pinned"},
        {"run_as_uid": 0},
        {"cgroup_root": Path("relative")},
        {"hooks_directory": Path("relative")},
    ],
)
def test_runtime_rejects_unsafe_configuration(kwargs: dict[str, object]) -> None:
    values: dict[str, object] = {
        "image_repository": IMAGE_REPOSITORY,
        "run_as_uid": 1001,
        "command": PodmanDouble(),
        "cgroup_root": Path("/sys/fs/cgroup"),
        "hooks_directory": HOOKS,
        "cgroup_remove": lambda path: None,
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        PodmanIsolationRuntime(**cast(Any, values))


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
        BoundedCommandRunner(Path("relative"), PodmanCommandLimits(1), environment={})
    missing = BoundedCommandRunner(
        tmp_path / "missing", PodmanCommandLimits(1), environment={}
    )
    with pytest.raises(PodmanRuntimeError, match="failed"):
        missing(("fixed",))
    failed = BoundedCommandRunner(
        Path("/bin/false"), PodmanCommandLimits(1), environment={}
    )
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
    with pytest.raises(PodmanRuntimeError, match="cleanup"):
        created_runtime(command, unit, policy)
    assert command.exists is True
    assert command.cgroup_removals == []

    command = PodmanDouble()
    command.status = "unknown"
    with pytest.raises(PodmanRuntimeError, match="state"):
        created_runtime(command, unit, policy)


@pytest.mark.unit
@pytest.mark.parametrize(
    "output",
    [
        CONTAINER_ID.encode(),
        (CONTAINER_ID + "\n").encode(),
    ],
)
def test_create_response_identity_accepts_only_canonical_output(output: bytes) -> None:
    assert podman_runtime._create_response_identity(output) == CONTAINER_ID


@pytest.mark.unit
@pytest.mark.parametrize(
    "output",
    [
        b"",
        (" " + CONTAINER_ID).encode(),
        (CONTAINER_ID + " ").encode(),
        (CONTAINER_ID + "\n\n").encode(),
        b"A" * 64,
        b"f" * 63,
        b"f" * 65,
        b"\xff" * 64,
    ],
)
def test_create_response_identity_rejects_noncanonical_output(output: bytes) -> None:
    assert podman_runtime._create_response_identity(output) is None


@pytest.mark.unit
def test_removal_rejects_live_unit_and_label_scoped_discovery(
    unit: ManagedUnit, policy: BrokerPolicy
) -> None:
    command = PodmanDouble()
    backend = runtime(command)
    runtime_unit = backend.create(unit, policy)
    with pytest.raises(PodmanRuntimeError, match="unconfirmed"):
        backend.confirm_removed(runtime_unit, EvidenceDigest(f"sha256:{'a' * 64}"))
    backend.hard_terminate(runtime_unit)
    empty_evidence = backend.confirm_empty(runtime_unit)
    backend.remove(runtime_unit)
    command.exists = True
    with pytest.raises(PodmanRuntimeError, match="unconfirmed"):
        backend.confirm_removed(runtime_unit, empty_evidence)


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
        if arguments[3] == "ps":
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
        cgroup_root=CGROUP_ROOT,
        hooks_directory=HOOKS,
        cgroup_remove=lambda path: None,
        hooks_directory_validate=lambda path: None,
        cgroup_root_validate=lambda path: None,
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
@pytest.mark.parametrize("output", [b"", b"populated 0\n", b"bad 0\nfrozen 0\n"])
def test_cgroup_parser_rejects_noncanonical_evidence(output: bytes) -> None:
    with pytest.raises(PodmanRuntimeError):
        podman_runtime._parse_cgroup_events(output)
