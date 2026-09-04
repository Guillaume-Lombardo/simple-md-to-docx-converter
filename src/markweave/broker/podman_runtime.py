"""Fail-closed rootless Podman lifecycle backend for reverse attempts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import selectors
import signal
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
_EVENTS_MAX_BYTES: Final = 128 * 1024
_MIN_OUTPUT_BYTES: Final = 1024
_MAX_LABEL_BYTES: Final = 128


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
            or not _MIN_OUTPUT_BYTES <= self.output_bytes <= _EVENTS_MAX_BYTES
        ):
            raise ValueError("Podman command limits are invalid")


class BoundedCommandRunner:
    """Run fixed argv without a shell under absolute time and output ceilings."""

    def __init__(
        self,
        executable: Path,
        limits: PodmanCommandLimits,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not isinstance(executable, Path)
            or not executable.is_absolute()
            or type(limits) is not PodmanCommandLimits
        ):
            raise ValueError("Podman command runner configuration is invalid")
        self._executable = executable
        self._limits = limits
        self._monotonic = monotonic

    def __call__(  # noqa: PLR0912 - cleanup branches enforce process bounds
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
        if type(ceiling) is not int or not 0 <= ceiling <= _EVENTS_MAX_BYTES:
            raise PodmanRuntimeError("Podman command contract failed")
        deadline = self._monotonic() + self._limits.operation_seconds
        try:
            process = subprocess.Popen(  # noqa: S603 - argv is broker-authored
                (str(self._executable), *arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as error:
            raise PodmanRuntimeError("Podman command failed") from error
        if process.stdout is None or process.stderr is None:
            self._terminate(process)
            raise PodmanRuntimeError("Podman command failed")
        output = bytearray()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, True)
        selector.register(process.stderr, selectors.EVENT_READ, False)
        try:
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
        except (OverflowError, subprocess.TimeoutExpired, TimeoutError) as error:
            self._terminate(process)
            raise PodmanRuntimeError("Podman command exceeded its bounds") from error
        finally:
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


class PodmanIsolationRuntime:
    """Rootless Podman backend implementing the shared isolation-runtime port."""

    def __init__(
        self,
        *,
        image_repository: str,
        run_as_uid: int,
        command: Command,
        event_lookback_seconds: int,
    ) -> None:
        if (
            type(image_repository) is not str
            or _IMAGE_REPOSITORY_PATTERN.fullmatch(image_repository) is None
            or ".." in image_repository.split("/")
            or len(image_repository) > _MAX_LABEL_BYTES
            or type(run_as_uid) is not int
            or run_as_uid <= 0
            or not callable(command)
            or type(event_lookback_seconds) is not int
            or event_lookback_seconds <= 0
        ):
            raise ValueError("Podman runtime configuration is invalid")
        self._image_repository = image_repository
        self._run_as_uid = run_as_uid
        self._command = command
        self._event_lookback_seconds = event_lookback_seconds
        self._environment_verified = False

    def create(self, unit: ManagedUnit, policy: BrokerPolicy) -> PodmanRuntimeUnit:
        """Create or recover and start one exact immutable-policy container."""

        self._require_create_contract(unit, policy)
        self._require_rootless_environment()
        labels = _labels(unit, policy, self._image_repository, self._run_as_uid)
        name = _container_name(unit.unit_id)
        create_code, output = self._command(
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
            self._command(("start", runtime_unit.container_id), max_output_bytes=1024)
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
            self._command(("start", verified.container_id), max_output_bytes=1024)
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
        """Digest positive runtime state proving no init, conmon, or exec member."""

        self._require_rootless_environment()
        verified = self._coerce_unit(runtime_unit)
        inspected = self._inspect(verified.container_id)
        self._verify_incarnation(inspected, verified)
        state = _mapping(inspected, "State")
        exec_ids = state.get("ExecIDs")
        if exec_ids is None:
            exec_ids = []
        conmon_pid = state.get("ConmonPid", 0)
        if (
            _boolean(state, "Running")
            or _integer(state, "Pid") != 0
            or type(conmon_pid) is not int
            or conmon_pid != 0
            or type(exec_ids) is not list
            or exec_ids
            or _string(state, "Status") not in {"exited", "stopped"}
        ):
            raise PodmanRuntimeError("Podman stable unit is not empty")
        return _evidence(
            "empty",
            {
                "conmon_pid": 0,
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
        code, _ = self._command(
            ("container", "exists", verified.name),
            max_output_bytes=0,
            accepted_exit_codes=frozenset({0, 1}),
        )
        if code == 0:
            inspected = self._inspect(verified.name)
            self._verify_incarnation(inspected, verified)
            actual = self._unit_from_labels(inspected)
            self._command(("rm", actual.container_id), max_output_bytes=1024)

    def confirm_removed(self, runtime_unit: RuntimeUnit) -> EvidenceDigest:
        """Require a durable matching Podman removal event, not mere absence."""

        self._require_rootless_environment()
        verified = self._coerce_unit(runtime_unit, allow_stored=True)
        code, _ = self._command(
            ("container", "exists", verified.name),
            max_output_bytes=0,
            accepted_exit_codes=frozenset({0, 1}),
        )
        if code == 0:
            raise PodmanRuntimeError("Podman removal is unconfirmed")
        _, output = self._command(
            (
                "events",
                "--stream=false",
                "--since",
                f"{self._event_lookback_seconds}s",
                "--filter",
                f"container={verified.name}",
                "--filter",
                "event=remove",
                "--format",
                "json",
            ),
            max_output_bytes=_EVENTS_MAX_BYTES,
        )
        events = _json_lines(output)
        matched = []
        for event in events:
            container_id = event.get("ID")
            if (
                event.get("Status") == "remove"
                and event.get("Name") == verified.name
                and type(container_id) is str
                and _CONTAINER_ID_PATTERN.fullmatch(container_id) is not None
                and uuid5(_INCARNATION_NAMESPACE, container_id)
                == verified.incarnation.incarnation_id
            ):
                matched.append(event)
        if len(matched) != 1:
            raise PodmanRuntimeError("Podman removal evidence is invalid")
        event = matched[0]
        return _evidence(
            "removed",
            {
                "container_id": matched[0]["ID"],
                "name": verified.name,
                "status": "remove",
                "time": _event_time(event),
            },
        )

    def discover(self, *, limit: int) -> tuple[PodmanRuntimeUnit, ...]:
        """Discover and validate every managed labelled container without truncation."""

        self._require_rootless_environment()
        if type(limit) is not int or limit <= 0:
            raise PodmanRuntimeError("Podman discovery limit is invalid")
        _, output = self._command(
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
            "--network=none",
            "--read-only",
            "--read-only-tmpfs=false",
            "--cap-drop=all",
            "--security-opt=no-new-privileges",
            "--user",
            f"{self._run_as_uid}:0",
            "--cgroups=enabled",
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
            "type=tmpfs,destination=/work,tmpfs-mode=0700,"
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

    def _require_rootless_environment(self) -> None:
        if self._environment_verified:
            return
        _, output = self._command(
            ("info", "--format", "json"), max_output_bytes=_INSPECT_MAX_BYTES
        )
        information = _json(output)
        if not isinstance(information, Mapping):
            raise PodmanRuntimeError("Podman environment evidence is invalid")
        host = _mapping(information, "host")
        security = _mapping(host, "security")
        if (
            security.get("rootless") is not True
            or security.get("seccompEnabled") is not True
            or host.get("cgroupVersion") != "v2"
            or host.get("eventLogger") not in {"file", "journald"}
        ):
            raise PodmanRuntimeError("Podman isolation environment is invalid")
        self._environment_verified = True

    def _kill_and_verify(self, runtime_unit: PodmanRuntimeUnit) -> None:
        self._command(
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
        _, output = self._command(
            ("container", "inspect", identity, "--format", "json"),
            max_output_bytes=_INSPECT_MAX_BYTES,
        )
        raw = _json(output)
        if type(raw) is not list or len(raw) != 1 or not isinstance(raw[0], Mapping):
            raise PodmanRuntimeError("Podman inspection evidence is invalid")
        return raw[0]

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
                self._create_arguments(
                    runtime_unit.name,
                    _labels(expected, policy, self._image_repository, self._run_as_uid),
                    policy,
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
                self._create_arguments(
                    _container_name(unit_id),
                    {key: _label(labels, key) for key in _MANAGED_LABEL_KEYS},
                    policy,
                ),
            )
        ):
            raise PodmanRuntimeError("Podman container specification is invalid")
        return PodmanRuntimeUnit(
            unit_id,
            RuntimeIncarnation(
                uuid5(_INCARNATION_NAMESPACE, container_id), specification
            ),
            container_id,
            _container_name(unit_id),
        )

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


def _json_lines(output: bytes) -> tuple[Mapping[str, Any], ...]:
    events: list[Mapping[str, Any]] = []
    try:
        text = output.decode("utf-8")
        for line in text.splitlines():
            value = json.loads(line)
            if not isinstance(value, Mapping):
                raise TypeError
            events.append(value)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise PodmanRuntimeError("Podman event evidence is invalid") from error
    return tuple(events)


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


def _event_time(event: Mapping[str, Any]) -> int:
    value = event.get("TimeNano", event.get("timeNano"))
    if type(value) is not int or value <= 0:
        raise PodmanRuntimeError("Podman event time is invalid")
    return value


def _evidence(kind: str, fields: Mapping[str, object]) -> EvidenceDigest:
    payload = json.dumps(
        {"fields": fields, "kind": kind, "schema_version": 1},
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return EvidenceDigest(f"sha256:{hashlib.sha256(payload).hexdigest()}")
