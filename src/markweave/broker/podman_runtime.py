"""Fail-closed rootless Podman lifecycle backend for reverse attempts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import signal
import stat
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from uuid import UUID, uuid5

from markweave.broker.models import (
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    RuntimeIncarnation,
    RuntimeLimits,
    policy_specification_evidence,
)
from markweave.broker.ports import RuntimeUnit

_INCARNATION_NAMESPACE: Final = UUID("9448db2f-5c64-48eb-a960-d520fac4fb5f")
_NAME_PREFIX: Final = "markweave-reverse-"
_CGROUP_SLICE: Final = "markweavet70.slice"
_LABEL_PREFIX: Final = "io.markweave.reverse-broker."
_MANAGED_LABEL: Final = f"{_LABEL_PREFIX}managed"
_UNIT_LABEL: Final = f"{_LABEL_PREFIX}unit-id"
_ATTEMPT_LABEL: Final = f"{_LABEL_PREFIX}attempt-id"
_PRINCIPAL_LABEL: Final = f"{_LABEL_PREFIX}principal-id"
_POLICY_LABEL: Final = f"{_LABEL_PREFIX}policy-revision"
_SPECIFICATION_LABEL: Final = f"{_LABEL_PREFIX}specification"
_DEADLINE_LABEL: Final = f"{_LABEL_PREFIX}deadline-seconds"
_IMAGE_DIGEST_LABEL: Final = f"{_LABEL_PREFIX}image-digest"
_IMAGE_REPOSITORY_LABEL: Final = f"{_LABEL_PREFIX}image-repository"
_RUN_AS_UID_LABEL: Final = f"{_LABEL_PREFIX}run-as-uid"
_CPU_QUOTA_LABEL: Final = f"{_LABEL_PREFIX}cpu-quota-micros"
_CPU_PERIOD_LABEL: Final = f"{_LABEL_PREFIX}cpu-period-micros"
_MEMORY_LABEL: Final = f"{_LABEL_PREFIX}memory-bytes"
_PID_LIMIT_LABEL: Final = f"{_LABEL_PREFIX}pid-limit"
_WORKSPACE_LABEL: Final = f"{_LABEL_PREFIX}workspace-bytes"
_WALL_TIME_LABEL: Final = f"{_LABEL_PREFIX}wall-time-millis"
_MANAGED_LABEL_KEYS: Final = frozenset(
    {
        _ATTEMPT_LABEL,
        _CPU_PERIOD_LABEL,
        _CPU_QUOTA_LABEL,
        _DEADLINE_LABEL,
        _IMAGE_DIGEST_LABEL,
        _IMAGE_REPOSITORY_LABEL,
        _MANAGED_LABEL,
        _MEMORY_LABEL,
        _PID_LIMIT_LABEL,
        _POLICY_LABEL,
        _PRINCIPAL_LABEL,
        _RUN_AS_UID_LABEL,
        _SPECIFICATION_LABEL,
        _UNIT_LABEL,
        _WORKSPACE_LABEL,
        _WALL_TIME_LABEL,
    }
)
_IMAGE_REPOSITORY_PATTERN = re.compile(
    r"(?:localhost|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?)(?::[0-9]{1,5})?"
    r"/[a-z0-9][a-z0-9._/-]*\Z"
)
_CONTAINER_ID_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]+(?:Z|[+-][0-9:]+)?\Z"
)
_FIXED_ENTRYPOINT: Final = (
    "python",
    "-m",
    "markweave.reversions.attempt_main",
)
_FIXED_ENVIRONMENT: Final = (
    "HOME=/work/home",
    "PATH=/opt/markweave/venv/bin:/usr/local/bin:/usr/bin",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONUNBUFFERED=1",
    "RAYON_NUM_THREADS=1",
    "TMPDIR=/work/tmp",
    "XDG_CACHE_HOME=/work/xdg/cache",
    "XDG_CONFIG_HOME=/work/xdg/config",
    "XDG_DATA_HOME=/work/xdg/data",
    "XDG_RUNTIME_DIR=/work/xdg/runtime",
)
_INSPECT_MAX_BYTES: Final = 64 * 1024
_MAX_COMMAND_OUTPUT_BYTES: Final = 128 * 1024
_MIN_OUTPUT_BYTES: Final = 1024
_MAX_LABEL_BYTES: Final = 128
_CGROUP_EVENTS_MAX_BYTES: Final = 4096
_OWNER_ONLY_MODE: Final = 0o700


class PodmanRuntimeError(RuntimeError):
    """Content-free Podman lifecycle failure."""


@dataclass(frozen=True, slots=True)
class PodmanRuntimeUnit:
    """Verified opaque identity of one exact Podman container incarnation."""

    unit_id: UUID
    incarnation: RuntimeIncarnation
    container_id: str
    name: str

    def __post_init__(self) -> None:
        if (
            type(self.unit_id) is not UUID
            or type(self.incarnation) is not RuntimeIncarnation
            or type(self.container_id) is not str
            or _CONTAINER_ID_PATTERN.fullmatch(self.container_id) is None
            or self.name != _container_name(self.unit_id)
        ):
            raise ValueError("Podman runtime unit identity is invalid")


@dataclass(frozen=True, slots=True)
class PodmanCommandLimits:
    """Broker-owned ceilings for every fixed Podman CLI operation."""

    operation_seconds: float
    output_bytes: int = _INSPECT_MAX_BYTES

    def __post_init__(self) -> None:
        if (
            type(self.operation_seconds) not in {int, float}
            or not math.isfinite(self.operation_seconds)
            or self.operation_seconds <= 0
            or type(self.output_bytes) is not int
            or not _MIN_OUTPUT_BYTES <= self.output_bytes <= _MAX_COMMAND_OUTPUT_BYTES
        ):
            raise ValueError("Podman command limits are invalid")


class BoundedCommandRunner:
    """Run fixed argv without a shell under absolute time and output ceilings."""

    def __init__(
        self,
        executable: Path,
        limits: PodmanCommandLimits,
        *,
        environment: Mapping[str, str],
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not isinstance(executable, Path)
            or not executable.is_absolute()
            or type(limits) is not PodmanCommandLimits
            or not isinstance(environment, Mapping)
            or any(
                type(key) is not str
                or type(value) is not str
                or not key
                or "=" in key
                or "\x00" in key
                or "\x00" in value
                for key, value in environment.items()
            )
            or "CONTAINER_HOST" in environment
        ):
            raise ValueError("Podman command runner configuration is invalid")
        self._executable = executable
        self._limits = limits
        self._environment = dict(environment)
        self._monotonic = monotonic

    def __call__(  # noqa: PLR0912,PLR0915 - cleanup branches enforce process bounds
        self,
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        if (
            not arguments
            or any(type(argument) is not str or not argument for argument in arguments)
            or not accepted_exit_codes
            or any(type(code) is not int for code in accepted_exit_codes)
        ):
            raise PodmanRuntimeError("Podman command contract failed")
        ceiling = (
            self._limits.output_bytes if max_output_bytes is None else max_output_bytes
        )
        if type(ceiling) is not int or not 0 <= ceiling <= _MAX_COMMAND_OUTPUT_BYTES:
            raise PodmanRuntimeError("Podman command contract failed")
        deadline = self._monotonic() + self._limits.operation_seconds
        try:
            process = subprocess.Popen(  # noqa: S603 - argv is broker-authored
                (str(self._executable), *arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                env=self._environment,
            )
        except OSError as error:
            raise PodmanRuntimeError("Podman command failed") from error
        if process.stdout is None or process.stderr is None:
            self._terminate(process)
            raise PodmanRuntimeError("Podman command failed")
        output = bytearray()
        selector: selectors.BaseSelector | None = None
        try:
            selector = selectors.DefaultSelector()
            selector.register(process.stdout, selectors.EVENT_READ, True)
            selector.register(process.stderr, selectors.EVENT_READ, False)
            while selector.get_map():
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    raise TimeoutError
                ready = selector.select(remaining)
                if not ready:
                    raise TimeoutError
                for key, _ in ready:
                    chunk = os.read(key.fd, 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    elif key.data:
                        output.extend(chunk)
                        if len(output) > ceiling:
                            raise OverflowError
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                raise TimeoutError
            return_code = process.wait(timeout=remaining)
        except BaseException as error:
            self._terminate(process)
            if isinstance(
                error, (OverflowError, subprocess.TimeoutExpired, TimeoutError)
            ):
                raise PodmanRuntimeError(
                    "Podman command exceeded its bounds"
                ) from error
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            raise PodmanRuntimeError("Podman command failed") from error
        finally:
            if selector is not None:
                selector.close()
            process.stdout.close()
            process.stderr.close()
        if return_code not in accepted_exit_codes:
            raise PodmanRuntimeError("Podman command failed")
        return return_code, bytes(output)

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes]) -> None:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired as error:
            raise PodmanRuntimeError("Podman command cleanup failed") from error


type Command = Callable[..., tuple[int, bytes]]


class SystemdCgroupRemover:
    """Stop one exact rootless systemd slice through a bounded local command."""

    def __init__(self, command: Command) -> None:
        if not callable(command):
            raise ValueError("Systemd cgroup remover configuration is invalid")
        self._command = command

    def __call__(self, path: Path) -> None:
        if (
            not isinstance(path, Path)
            or not path.is_absolute()
            or re.fullmatch(r"markweavet70-[0-9a-f]{32}\.slice", path.name) is None
        ):
            raise PodmanRuntimeError("Podman cgroup cleanup identity is invalid")
        self._command(("--user", "stop", path.name), max_output_bytes=0)
        try:
            path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return
        except OSError as error:
            raise PodmanRuntimeError("Podman cgroup cleanup failed") from error
        raise PodmanRuntimeError("Podman cgroup cleanup is unconfirmed")


class PodmanIsolationRuntime:
    """Rootless Podman backend implementing the shared isolation-runtime port."""

    def __init__(  # noqa: PLR0913 - security dependencies are explicit
        self,
        *,
        image_repository: str,
        run_as_uid: int,
        command: Command,
        cgroup_root: Path,
        hooks_directory: Path,
        cgroup_remove: Callable[[Path], None],
        hooks_directory_validate: Callable[[Path], None] | None = None,
        cgroup_root_validate: Callable[[Path], None] | None = None,
        cgroup_create: Callable[[Path], None] | None = None,
        cgroup_read: Callable[[Path], bytes] | None = None,
        process_cgroup_read: Callable[[int], bytes] | None = None,
    ) -> None:
        if (
            type(image_repository) is not str
            or _IMAGE_REPOSITORY_PATTERN.fullmatch(image_repository) is None
            or ".." in image_repository.split("/")
            or len(image_repository) > _MAX_LABEL_BYTES
            or type(run_as_uid) is not int
            or run_as_uid <= 0
            or not callable(command)
            or not isinstance(cgroup_root, Path)
            or not cgroup_root.is_absolute()
            or not isinstance(hooks_directory, Path)
            or not hooks_directory.is_absolute()
            or (cgroup_read is not None and not callable(cgroup_read))
            or (cgroup_create is not None and not callable(cgroup_create))
            or not callable(cgroup_remove)
            or (process_cgroup_read is not None and not callable(process_cgroup_read))
            or (cgroup_root_validate is not None and not callable(cgroup_root_validate))
            or (
                hooks_directory_validate is not None
                and not callable(hooks_directory_validate)
            )
        ):
            raise ValueError("Podman runtime configuration is invalid")
        self._image_repository = image_repository
        self._run_as_uid = run_as_uid
        self._command = command
        self._cgroup_root = cgroup_root
        self._hooks_directory = hooks_directory
        self._hooks_directory_validate = (
            hooks_directory_validate or _validate_hooks_directory
        )
        self._cgroup_create = cgroup_create or _create_cgroup
        self._cgroup_read = cgroup_read or _read_cgroup_events
        self._process_cgroup_read = process_cgroup_read or _read_process_cgroup
        self._cgroup_remove = cgroup_remove
        self._cgroup_root_validate = cgroup_root_validate or _validate_cgroup_root
        self._environment_verified = False
        self._runtime_capabilities: tuple[str, ...] = ()

    def create(self, unit: ManagedUnit, policy: BrokerPolicy) -> PodmanRuntimeUnit:
        """Create or recover and start one exact immutable-policy container."""

        self._require_create_contract(unit, policy)
        self._require_rootless_environment()
        labels = _labels(unit, policy, self._image_repository, self._run_as_uid)
        name = _container_name(unit.unit_id)
        self._cgroup_create(self._cgroup_path(unit.unit_id))
        create_code, output = self._call(
            self._create_arguments(name, labels, policy),
            max_output_bytes=1024,
            accepted_exit_codes=frozenset({0, 125}),
        )
        try:
            created_id = output.strip().decode("ascii", errors="strict")
        except UnicodeDecodeError as error:
            raise PodmanRuntimeError("Podman create identity is invalid") from error
        inspected = self._inspect(name)
        runtime_unit = self._verified_unit(inspected, expected=unit, policy=policy)
        if (create_code == 0 and created_id != runtime_unit.container_id) or (
            create_code != 0 and created_id
        ):
            raise PodmanRuntimeError("Podman create identity is invalid")
        state = _mapping(inspected, "State")
        status = _string(state, "Status")
        if status == "created":
            self._call(("start", runtime_unit.container_id), max_output_bytes=1024)
            runtime_unit = self._verified_unit(
                self._inspect(runtime_unit.container_id), expected=unit, policy=policy
            )
            status = _string(
                _mapping(self._inspect(runtime_unit.container_id), "State"), "Status"
            )
        if status not in {"running", "exited", "stopped"}:
            raise PodmanRuntimeError("Podman container state is invalid")
        return runtime_unit

    def hard_terminate(self, runtime_unit: RuntimeUnit) -> None:
        """Send SIGKILL to the verified complete container unit, idempotently."""

        self._require_rootless_environment()
        verified = self._coerce_unit(runtime_unit)
        inspected = self._inspect(verified.container_id)
        self._verify_incarnation(inspected, verified)
        if _boolean(_mapping(inspected, "State"), "Running"):
            self._kill_and_verify(verified)
        elif _string(_mapping(inspected, "State"), "Status") == "created":
            self._call(("start", verified.container_id), max_output_bytes=1024)
            inspected = self._inspect(verified.container_id)
            self._verify_incarnation(inspected, verified)
            if _boolean(_mapping(inspected, "State"), "Running"):
                self._kill_and_verify(verified)

    def confirm_exit(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        """Digest positive allowlisted Podman exit state for this incarnation."""

        self._require_rootless_environment()
        verified = self._coerce_unit(runtime_unit)
        inspected = self._inspect(verified.container_id)
        self._verify_incarnation(inspected, verified)
        state = _mapping(inspected, "State")
        if (
            _boolean(state, "Running")
            or _boolean(state, "Paused")
            or _boolean(state, "Restarting")
            or _string(state, "Status") not in {"exited", "stopped"}
        ):
            raise PodmanRuntimeError("Podman exit is unconfirmed")
        return _evidence(
            "exit",
            {
                "container_id": verified.container_id,
                "exit_code": _integer(state, "ExitCode"),
                "finished_at": _timestamp(state, "FinishedAt"),
                "oom_killed": _boolean(state, "OOMKilled"),
            },
        )

    def confirm_empty(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        """Digest positive runtime and cgroup-v2 whole-unit emptiness evidence."""

        self._require_rootless_environment()
        verified = self._coerce_unit(runtime_unit)
        inspected = self._inspect(verified.container_id)
        self._verify_incarnation(inspected, verified)
        state = _mapping(inspected, "State")
        exec_ids = inspected.get("ExecIDs")
        if (
            _boolean(state, "Running")
            or _integer(state, "Pid") != 0
            or type(exec_ids) is not list
            or exec_ids
            or _string(state, "Status") not in {"exited", "stopped"}
        ):
            raise PodmanRuntimeError("Podman stable unit is not empty")
        cgroup_events = _parse_cgroup_events(
            self._cgroup_read(self._cgroup_path(verified.unit_id))
        )
        if cgroup_events.get("populated") != 0:
            raise PodmanRuntimeError("Podman stable unit cgroup is not empty")
        return _evidence(
            "empty",
            {
                "cgroup_frozen": cgroup_events.get("frozen", 0),
                "cgroup_populated": 0,
                "container_id": verified.container_id,
                "exec_count": 0,
                "init_pid": 0,
                "status": _string(state, "Status"),
            },
        )

    def remove(self, runtime_unit: RuntimeUnit) -> None:
        """Request removal after caller durably records positive emptiness."""

        self._require_rootless_environment()
        verified = self._coerce_unit(runtime_unit, allow_stored=True)
        code, _ = self._call(
            ("container", "exists", verified.name),
            max_output_bytes=0,
            accepted_exit_codes=frozenset({0, 1}),
        )
        if code == 0:
            inspected = self._inspect(verified.name)
            self._verify_incarnation(inspected, verified)
            actual = self._unit_from_labels(inspected)
            self._call(("rm", actual.container_id), max_output_bytes=1024)

    def confirm_removed(
        self, runtime_unit: RuntimeUnit, empty_evidence: EvidenceDigest
    ) -> EvidenceDigest:
        """Bind persisted emptiness to two bounded post-delete absence queries."""

        self._require_rootless_environment()
        verified = self._coerce_unit(runtime_unit, allow_stored=True)
        if type(empty_evidence) is not EvidenceDigest:
            raise PodmanRuntimeError("Podman empty evidence is invalid")
        code, _ = self._call(
            ("container", "exists", verified.name),
            max_output_bytes=0,
            accepted_exit_codes=frozenset({0, 1}),
        )
        if code == 0:
            raise PodmanRuntimeError("Podman removal is unconfirmed")
        _, output = self._call(
            (
                "ps",
                "--all",
                "--no-trunc",
                "--filter",
                f"label={_UNIT_LABEL}={verified.unit_id}",
                "--format",
                "json",
            ),
            max_output_bytes=_INSPECT_MAX_BYTES,
        )
        discovered = _json(output)
        if type(discovered) is not list or discovered:
            raise PodmanRuntimeError("Podman removal is unconfirmed")
        cgroup = self._cgroup_path(verified.unit_id)
        try:
            self._cgroup_remove(cgroup)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise PodmanRuntimeError("Podman cgroup cleanup failed") from error
        return _evidence(
            "removed",
            {
                "empty_evidence": empty_evidence.value,
                "incarnation_id": str(verified.incarnation.incarnation_id),
                "name": verified.name,
                "runtime_discovery_count": 0,
                "runtime_name_exists": False,
                "unit_id": str(verified.unit_id),
            },
        )

    def discover(self, *, limit: int) -> tuple[PodmanRuntimeUnit, ...]:
        """Discover and validate every managed labelled container without truncation."""

        self._require_rootless_environment()
        if type(limit) is not int or limit <= 0:
            raise PodmanRuntimeError("Podman discovery limit is invalid")
        _, output = self._call(
            (
                "ps",
                "--all",
                "--no-trunc",
                "--filter",
                f"label={_MANAGED_LABEL}=1",
                "--format",
                "json",
            ),
            max_output_bytes=_INSPECT_MAX_BYTES,
        )
        raw = _json(output)
        if type(raw) is not list or len(raw) > limit:
            raise PodmanRuntimeError("Podman discovery exceeds its limit")
        discovered: list[PodmanRuntimeUnit] = []
        seen: set[UUID] = set()
        for summary in raw:
            if not isinstance(summary, Mapping):
                raise PodmanRuntimeError("Podman discovery evidence is invalid")
            names = summary.get("Names")
            if type(names) is not list or len(names) != 1 or type(names[0]) is not str:
                raise PodmanRuntimeError("Podman discovery identity is invalid")
            inspected = self._inspect(names[0])
            runtime_unit = self._unit_from_labels(inspected)
            if runtime_unit.unit_id in seen:
                raise PodmanRuntimeError("Podman discovery identity is duplicated")
            seen.add(runtime_unit.unit_id)
            discovered.append(runtime_unit)
        return tuple(sorted(discovered, key=lambda item: str(item.unit_id)))

    def _create_arguments(
        self, name: str, labels: Mapping[str, str], policy: BrokerPolicy
    ) -> tuple[str, ...]:
        seconds = math.ceil(policy.limits.wall_time_millis / 1000)
        arguments = [
            "create",
            "--pull=never",
            "--name",
            name,
            "--hostname",
            name,
            "--network=none",
            "--read-only",
            "--read-only-tmpfs=false",
            "--cap-drop=all",
            "--security-opt=no-new-privileges",
            "--user",
            f"{self._run_as_uid}:0",
            "--cgroups=enabled",
            "--cgroup-parent",
            _cgroup_parent(name),
            "--ipc=none",
            "--pid=private",
            "--uts=private",
            "--restart=no",
            "--no-healthcheck",
            "--log-driver=none",
            "--pids-limit",
            str(policy.limits.pid_limit),
            "--memory",
            f"{policy.limits.memory_bytes}b",
            "--memory-swap",
            f"{policy.limits.memory_bytes}b",
            "--cpu-period",
            str(policy.limits.cpu_period_micros),
            "--cpu-quota",
            str(policy.limits.cpu_quota_micros),
            "--timeout",
            str(seconds),
            "--stop-timeout",
            "0",
            "--unsetenv-all",
            "--mount",
            "type=tmpfs,destination=/work,tmpfs-mode=0770,"
            f"tmpfs-size={policy.limits.workspace_bytes}",
            "--workdir=/work",
            "--entrypoint",
            json.dumps(_FIXED_ENTRYPOINT, separators=(",", ":")),
        ]
        for environment in _FIXED_ENVIRONMENT:
            arguments.extend(("--env", environment))
        for key in sorted(labels):
            arguments.extend(("--label", f"{key}={labels[key]}"))
        arguments.append(f"{self._image_repository}@{policy.image_digest}")
        return tuple(arguments)

    def _cgroup_path(self, unit_id: UUID) -> Path:
        parent = _cgroup_parent(_container_name(unit_id))
        return self._cgroup_root / _CGROUP_SLICE / parent

    def _require_rootless_environment(self) -> None:
        if self._environment_verified:
            return
        self._hooks_directory_validate(self._hooks_directory)
        _, output = self._call(
            ("info", "--format", "json"), max_output_bytes=_INSPECT_MAX_BYTES
        )
        information = _json(output)
        if not isinstance(information, Mapping):
            raise PodmanRuntimeError("Podman environment evidence is invalid")
        host = _mapping(information, "host")
        security = _mapping(host, "security")
        capabilities = security.get("capabilities")
        if (
            security.get("rootless") is not True
            or security.get("seccompEnabled") is not True
            or host.get("cgroupVersion") != "v2"
            or host.get("cgroupManager") != "systemd"
            or host.get("serviceIsRemote") is not False
            or type(capabilities) is not str
            or not capabilities
        ):
            raise PodmanRuntimeError("Podman isolation environment is invalid")
        self._cgroup_root_validate(self._cgroup_root)
        parsed = tuple(capabilities.split(","))
        if len(set(parsed)) != len(parsed) or any(
            not item.startswith("CAP_") or not item.isascii() for item in parsed
        ):
            raise PodmanRuntimeError("Podman isolation environment is invalid")
        self._runtime_capabilities = parsed
        self._environment_verified = True

    def _kill_and_verify(self, runtime_unit: PodmanRuntimeUnit) -> None:
        self._call(
            ("kill", "--signal", "KILL", runtime_unit.container_id),
            max_output_bytes=1024,
            accepted_exit_codes=frozenset({0, 125}),
        )
        inspected = self._inspect(runtime_unit.container_id)
        self._verify_incarnation(inspected, runtime_unit)
        state = _mapping(inspected, "State")
        if _boolean(state, "Running") or _string(state, "Status") not in {
            "exited",
            "stopped",
        }:
            raise PodmanRuntimeError("Podman whole-unit termination is unconfirmed")

    def _inspect(self, identity: str) -> Mapping[str, Any]:
        _, output = self._call(
            ("container", "inspect", identity, "--format", "json"),
            max_output_bytes=_INSPECT_MAX_BYTES,
        )
        raw = _json(output)
        if type(raw) is not list or len(raw) != 1 or not isinstance(raw[0], Mapping):
            raise PodmanRuntimeError("Podman inspection evidence is invalid")
        return raw[0]

    def _call(
        self,
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        return self._command(
            self._podman_arguments(arguments),
            max_output_bytes=max_output_bytes,
            accepted_exit_codes=accepted_exit_codes,
        )

    def _podman_arguments(self, arguments: Sequence[str]) -> tuple[str, ...]:
        return (
            "--remote=false",
            "--hooks-dir",
            str(self._hooks_directory),
            *arguments,
        )

    def _verified_unit(
        self,
        inspected: Mapping[str, Any],
        *,
        expected: ManagedUnit,
        policy: BrokerPolicy,
    ) -> PodmanRuntimeUnit:
        runtime_unit = self._unit_from_labels(inspected)
        if (
            runtime_unit.unit_id != expected.unit_id
            or runtime_unit.incarnation.specification != expected.policy_specification
            or _string(inspected, "ImageDigest") != policy.image_digest
            or _string(inspected, "ImageName")
            != f"{self._image_repository}@{policy.image_digest}"
            or not _matches_create_command(
                _mapping(inspected, "Config").get("CreateCommand"),
                self._podman_arguments(
                    self._create_arguments(
                        runtime_unit.name,
                        _labels(
                            expected,
                            policy,
                            self._image_repository,
                            self._run_as_uid,
                        ),
                        policy,
                    )
                ),
            )
        ):
            raise PodmanRuntimeError("Podman container specification is invalid")
        expected_labels = _labels(
            expected, policy, self._image_repository, self._run_as_uid
        )
        actual_labels = _mapping(_mapping(inspected, "Config"), "Labels")
        if any(
            actual_labels.get(key) != value for key, value in expected_labels.items()
        ):
            raise PodmanRuntimeError("Podman container labels are invalid")
        self._verify_realized_specification(inspected, policy)
        self._verify_cgroup_binding(inspected, runtime_unit)
        return runtime_unit

    def _unit_from_labels(self, inspected: Mapping[str, Any]) -> PodmanRuntimeUnit:
        container_id = _string(inspected, "Id")
        if _CONTAINER_ID_PATTERN.fullmatch(container_id) is None:
            raise PodmanRuntimeError("Podman container identity is invalid")
        labels = _mapping(_mapping(inspected, "Config"), "Labels")
        try:
            unit_id = UUID(_label(labels, _UNIT_LABEL))
            UUID(_label(labels, _ATTEMPT_LABEL))
            UUID(_label(labels, _PRINCIPAL_LABEL))
            specification = EvidenceDigest(_label(labels, _SPECIFICATION_LABEL))
        except (ValueError, TypeError) as error:
            raise PodmanRuntimeError("Podman container labels are invalid") from error
        managed_keys = frozenset(
            key for key in labels if type(key) is str and key.startswith(_LABEL_PREFIX)
        )
        if managed_keys != _MANAGED_LABEL_KEYS:
            raise PodmanRuntimeError("Podman container labels are invalid")
        if _label(labels, _MANAGED_LABEL) != "1" or _label(labels, _POLICY_LABEL) == "":
            raise PodmanRuntimeError("Podman container labels are invalid")
        deadline = _label(labels, _DEADLINE_LABEL)
        if not deadline.isascii() or not deadline.isdigit() or int(deadline) <= 0:
            raise PodmanRuntimeError("Podman container deadline is invalid")
        names = inspected.get("Name")
        if type(names) is not str or names.lstrip("/") != _container_name(unit_id):
            raise PodmanRuntimeError("Podman container name is invalid")
        policy = _policy_from_labels(labels)
        if (
            policy_specification_evidence(policy) != specification
            or _positive_integer_label(labels, _DEADLINE_LABEL)
            != math.ceil(policy.limits.wall_time_millis / 1000)
            or _label(labels, _IMAGE_REPOSITORY_LABEL) != self._image_repository
            or _positive_integer_label(labels, _RUN_AS_UID_LABEL) != self._run_as_uid
            or _string(inspected, "ImageDigest") != policy.image_digest
            or _string(inspected, "ImageName")
            != f"{self._image_repository}@{policy.image_digest}"
            or not _matches_create_command(
                _mapping(inspected, "Config").get("CreateCommand"),
                self._podman_arguments(
                    self._create_arguments(
                        _container_name(unit_id),
                        {key: _label(labels, key) for key in _MANAGED_LABEL_KEYS},
                        policy,
                    )
                ),
            )
        ):
            raise PodmanRuntimeError("Podman container specification is invalid")
        self._verify_realized_specification(inspected, policy)
        runtime_unit = PodmanRuntimeUnit(
            unit_id,
            RuntimeIncarnation(
                uuid5(_INCARNATION_NAMESPACE, container_id), specification
            ),
            container_id,
            _container_name(unit_id),
        )
        self._verify_cgroup_binding(inspected, runtime_unit)
        return runtime_unit

    def _verify_cgroup_binding(
        self, inspected: Mapping[str, Any], runtime_unit: PodmanRuntimeUnit
    ) -> None:
        state = _mapping(inspected, "State")
        status = _string(state, "Status")
        if status == "created":
            if state.get("CgroupPath") not in {None, ""}:
                raise PodmanRuntimeError("Podman cgroup binding is invalid")
            return
        expected = self._runtime_cgroup_path(runtime_unit)
        running = _boolean(state, "Running")
        if running and state.get("CgroupPath") != expected:
            raise PodmanRuntimeError("Podman cgroup binding is invalid")
        if not running and state.get("CgroupPath") not in {None, "", expected}:
            raise PodmanRuntimeError("Podman cgroup binding is invalid")
        if running:
            pid = _integer(state, "Pid")
            if (
                pid <= 0
                or self._process_cgroup_read(pid) != f"0::{expected}\n".encode()
            ):
                raise PodmanRuntimeError("Podman cgroup binding is invalid")
            events = _parse_cgroup_events(
                self._cgroup_read(self._cgroup_path(runtime_unit.unit_id))
            )
            if events.get("populated") != 1:
                raise PodmanRuntimeError("Podman cgroup binding is invalid")

    def _runtime_cgroup_path(self, runtime_unit: PodmanRuntimeUnit) -> str:
        try:
            relative = self._cgroup_path(runtime_unit.unit_id).relative_to(
                Path("/sys/fs/cgroup")
            )
        except ValueError as error:
            raise PodmanRuntimeError("Podman cgroup root is invalid") from error
        return f"/{relative}/libpod-{runtime_unit.container_id}.scope"

    def _verify_realized_specification(
        self, inspected: Mapping[str, Any], policy: BrokerPolicy
    ) -> None:
        config = _mapping(inspected, "Config")
        host = _mapping(inspected, "HostConfig")
        expected_config: Mapping[str, object] = {
            "Cmd": None,
            "Entrypoint": list(_FIXED_ENTRYPOINT),
            "StopTimeout": 0,
            "Timeout": math.ceil(policy.limits.wall_time_millis / 1000),
            "User": f"{self._run_as_uid}:0",
            "WorkingDir": "/work",
        }
        expected_host: Mapping[str, object] = {
            "AutoRemove": False,
            "Binds": [],
            "CapAdd": [],
            "CapDrop": list(self._runtime_capabilities),
            "CgroupParent": _cgroup_parent(_string(inspected, "Name").lstrip("/")),
            "Cgroups": "default",
            "CpuPeriod": policy.limits.cpu_period_micros,
            "CpuQuota": policy.limits.cpu_quota_micros,
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
            "Memory": policy.limits.memory_bytes,
            "MemorySwap": policy.limits.memory_bytes,
            "NetworkMode": "none",
            "PidMode": "private",
            "PidsLimit": policy.limits.pid_limit,
            "PortBindings": {},
            "Privileged": False,
            "PublishAllPorts": False,
            "ReadonlyRootfs": True,
            "RestartPolicy": {"MaximumRetryCount": 0, "Name": "no"},
            "SecurityOpt": ["no-new-privileges"],
            "Tmpfs": {
                "/work": (
                    f"mode=0770,size={policy.limits.workspace_bytes},"
                    "rw,rprivate,nosuid,nodev,tmpcopyup"
                )
            },
            "UTSMode": "private",
            "VolumesFrom": None,
        }
        if (
            type(config.get("Env")) is not list
            or sorted(config["Env"])
            != sorted(
                (
                    *_FIXED_ENVIRONMENT,
                    f"HOSTNAME={_string(inspected, 'Name').lstrip('/')}",
                )
            )
            or any(config.get(key) != value for key, value in expected_config.items())
            or any(host.get(key) != value for key, value in expected_host.items())
            or inspected.get("Mounts") != []
        ):
            raise PodmanRuntimeError("Podman realized specification is invalid")

    def _verify_incarnation(
        self, inspected: Mapping[str, Any], expected: PodmanRuntimeUnit
    ) -> None:
        actual = self._unit_from_labels(inspected)
        exact_container_mismatch = expected.container_id not in {
            "0" * 64,
            actual.container_id,
        }
        if (
            actual.unit_id != expected.unit_id
            or actual.incarnation != expected.incarnation
            or actual.name != expected.name
            or exact_container_mismatch
        ):
            raise PodmanRuntimeError("Podman runtime incarnation is invalid")

    @staticmethod
    def _require_create_contract(unit: ManagedUnit, policy: BrokerPolicy) -> None:
        if (
            type(unit) is not ManagedUnit
            or type(policy) is not BrokerPolicy
            or unit.state is not ManagedUnitState.CREATE_INTENT
            or unit.policy_revision != policy.revision
            or unit.policy_specification != policy_specification_evidence(policy)
        ):
            raise PodmanRuntimeError("Podman create contract failed")

    @staticmethod
    def _coerce_unit(
        runtime_unit: RuntimeUnit, *, allow_stored: bool = False
    ) -> PodmanRuntimeUnit:
        if type(runtime_unit) is PodmanRuntimeUnit:
            return runtime_unit
        unit_id = getattr(runtime_unit, "unit_id", None)
        incarnation = getattr(runtime_unit, "incarnation", None)
        if (
            allow_stored
            and type(unit_id) is UUID
            and type(incarnation) is RuntimeIncarnation
        ):
            # The deterministic name and persisted incarnation allow post-removal event recovery.
            return PodmanRuntimeUnit(
                unit_id,
                incarnation,
                "0" * 64,
                _container_name(unit_id),
            )
        raise PodmanRuntimeError("Podman runtime identity is invalid")


def _container_name(unit_id: UUID) -> str:
    return f"{_NAME_PREFIX}{unit_id.hex}"


def _cgroup_parent(container_name: str) -> str:
    if not container_name.startswith(_NAME_PREFIX):
        raise PodmanRuntimeError("Podman cgroup identity is invalid")
    return f"markweavet70-{container_name.removeprefix(_NAME_PREFIX)}.slice"


def _read_cgroup_events(path: Path) -> bytes:
    try:
        descriptor = os.open(
            path / "cgroup.events", os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
        )
        try:
            value = os.read(descriptor, _CGROUP_EVENTS_MAX_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise PodmanRuntimeError("Podman cgroup evidence is unavailable") from error
    if len(value) > _CGROUP_EVENTS_MAX_BYTES:
        raise PodmanRuntimeError("Podman cgroup evidence exceeded its bound")
    return value


def _create_cgroup(path: Path) -> None:
    for directory in (path.parent, path):
        with suppress(FileExistsError):
            directory.mkdir(mode=_OWNER_ONLY_MODE)
        try:
            metadata = directory.stat(follow_symlinks=False)
            (directory / "cgroup.events").stat(follow_symlinks=False)
        except OSError as error:
            raise PodmanRuntimeError("Podman cgroup preparation failed") from error
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise PodmanRuntimeError("Podman cgroup preparation failed")


def _validate_cgroup_root(path: Path) -> None:
    expected = Path(
        f"/sys/fs/cgroup/user.slice/user-{os.geteuid()}.slice/"
        f"user@{os.geteuid()}.service"
    )
    try:
        resolved = path.resolve(strict=True)
        metadata = path.stat(follow_symlinks=False)
        (path / "cgroup.controllers").stat(follow_symlinks=False)
        (path / "cgroup.events").stat(follow_symlinks=False)
    except OSError as error:
        raise PodmanRuntimeError("Podman cgroup root is invalid") from error
    if (
        path != expected
        or resolved != expected
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
    ):
        raise PodmanRuntimeError("Podman cgroup root is invalid")


def _read_process_cgroup(pid: int) -> bytes:
    if type(pid) is not int or pid <= 0:
        raise PodmanRuntimeError("Podman cgroup binding is invalid")
    path = Path("/proc") / str(pid) / "cgroup"
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            value = os.read(descriptor, _CGROUP_EVENTS_MAX_BYTES + 1)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise PodmanRuntimeError("Podman cgroup binding is unavailable") from error
    if len(value) > _CGROUP_EVENTS_MAX_BYTES:
        raise PodmanRuntimeError("Podman cgroup binding exceeded its bound")
    return value


def _validate_hooks_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            metadata = os.fstat(descriptor)
            entries = os.listdir(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise PodmanRuntimeError("Podman hooks directory is invalid") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != _OWNER_ONLY_MODE
        or entries
    ):
        raise PodmanRuntimeError("Podman hooks directory is invalid")


def _parse_cgroup_events(value: bytes) -> dict[str, int]:
    if type(value) is not bytes or not value or len(value) > _CGROUP_EVENTS_MAX_BYTES:
        raise PodmanRuntimeError("Podman cgroup evidence is invalid")
    parsed: dict[str, int] = {}
    try:
        text = value.decode("ascii")
        for line in text.splitlines():
            key, raw = line.split(" ", 1)
            if key not in {"frozen", "populated"} or key in parsed:
                raise ValueError
            parsed[key] = int(raw)
    except (UnicodeDecodeError, ValueError) as error:
        raise PodmanRuntimeError("Podman cgroup evidence is invalid") from error
    if set(parsed) != {"frozen", "populated"} or any(
        item not in {0, 1} for item in parsed.values()
    ):
        raise PodmanRuntimeError("Podman cgroup evidence is invalid")
    return parsed


def _labels(
    unit: ManagedUnit,
    policy: BrokerPolicy,
    image_repository: str,
    run_as_uid: int,
) -> dict[str, str]:
    seconds = math.ceil(policy.limits.wall_time_millis / 1000)
    return {
        _ATTEMPT_LABEL: str(unit.attempt_id),
        _DEADLINE_LABEL: str(seconds),
        _CPU_PERIOD_LABEL: str(policy.limits.cpu_period_micros),
        _CPU_QUOTA_LABEL: str(policy.limits.cpu_quota_micros),
        _IMAGE_DIGEST_LABEL: policy.image_digest,
        _IMAGE_REPOSITORY_LABEL: image_repository,
        _MANAGED_LABEL: "1",
        _MEMORY_LABEL: str(policy.limits.memory_bytes),
        _PID_LIMIT_LABEL: str(policy.limits.pid_limit),
        _POLICY_LABEL: unit.policy_revision,
        _PRINCIPAL_LABEL: str(unit.principal.principal_id),
        _SPECIFICATION_LABEL: unit.policy_specification.value,
        _UNIT_LABEL: str(unit.unit_id),
        _RUN_AS_UID_LABEL: str(run_as_uid),
        _WORKSPACE_LABEL: str(policy.limits.workspace_bytes),
        _WALL_TIME_LABEL: str(policy.limits.wall_time_millis),
    }


def _policy_from_labels(labels: Mapping[str, Any]) -> BrokerPolicy:
    try:
        return BrokerPolicy(
            _label(labels, _POLICY_LABEL),
            _label(labels, _IMAGE_DIGEST_LABEL),
            RuntimeLimits(
                _positive_integer_label(labels, _CPU_QUOTA_LABEL),
                _positive_integer_label(labels, _CPU_PERIOD_LABEL),
                _positive_integer_label(labels, _MEMORY_LABEL),
                _positive_integer_label(labels, _PID_LIMIT_LABEL),
                _positive_integer_label(labels, _WORKSPACE_LABEL),
                _positive_integer_label(labels, _WALL_TIME_LABEL),
            ),
        )
    except ValueError as error:
        raise PodmanRuntimeError("Podman policy labels are invalid") from error


def _json(output: bytes) -> Any:
    try:
        return json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PodmanRuntimeError("Podman JSON evidence is invalid") from error


def _matches_create_command(value: Any, arguments: tuple[str, ...]) -> bool:
    return (
        type(value) is list
        and len(value) == len(arguments) + 1
        and all(type(item) is str for item in value)
        and Path(value[0]).name == "podman"
        and value[1:] == list(arguments)
    )


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, Mapping):
        raise PodmanRuntimeError("Podman evidence field is invalid")
    return nested


def _string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if type(item) is not str:
        raise PodmanRuntimeError("Podman evidence field is invalid")
    return item


def _boolean(value: Mapping[str, Any], key: str) -> bool:
    item = value.get(key)
    if type(item) is not bool:
        raise PodmanRuntimeError("Podman evidence field is invalid")
    return item


def _integer(value: Mapping[str, Any], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise PodmanRuntimeError("Podman evidence field is invalid")
    return item


def _timestamp(value: Mapping[str, Any], key: str) -> str:
    item = _string(value, key)
    if _TIMESTAMP_PATTERN.fullmatch(item) is None:
        raise PodmanRuntimeError("Podman timestamp evidence is invalid")
    return item


def _label(labels: Mapping[str, Any], key: str) -> str:
    value = labels.get(key)
    if type(value) is not str or len(value) > _MAX_LABEL_BYTES:
        raise PodmanRuntimeError("Podman label evidence is invalid")
    return value


def _positive_integer_label(labels: Mapping[str, Any], key: str) -> int:
    value = _label(labels, key)
    if not value.isascii() or not value.isdigit() or int(value) <= 0:
        raise PodmanRuntimeError("Podman numeric label evidence is invalid")
    return int(value)


def _evidence(kind: str, fields: Mapping[str, object]) -> EvidenceDigest:
    payload = json.dumps(
        {"fields": fields, "kind": kind, "schema_version": 1},
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return EvidenceDigest(f"sha256:{hashlib.sha256(payload).hexdigest()}")
