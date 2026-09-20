"""Fail-closed rootless Podman lifecycle backend for reverse attempts."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import markweave.broker.podman_cgroups as _podman_cgroups
import markweave.broker.podman_contract as _podman_contract
import markweave.broker.podman_workspace as _podman_workspace
from markweave.broker import command_runner as _command_runner
from markweave.broker.models import (
    BrokerPolicy,
    EvidenceDigest,
    ManagedUnit,
    ManagedUnitState,
    RuntimeIncarnation,
    RuntimeRecoveryBinding,
    policy_specification_evidence,
)
from markweave.broker.ports import RuntimeUnit
from markweave.reversions.attempt_channel import (
    MAX_METADATA_BYTES,
    decode_channel_state,
    decode_response_metadata,
)
from markweave.reversions.errors import ReverseConversionError
from markweave.reversions.models import (
    ReverseAttemptRequest,
    ReverseAttemptResponse,
)

PodmanRuntimeError = _podman_contract.PodmanRuntimeError
_INCARNATION_NAMESPACE = _podman_contract._INCARNATION_NAMESPACE
_NAME_PREFIX = _podman_contract._NAME_PREFIX
_LABEL_PREFIX = _podman_contract._LABEL_PREFIX
_MANAGED_LABEL = _podman_contract._MANAGED_LABEL
_UNIT_LABEL = _podman_contract._UNIT_LABEL
_ATTEMPT_LABEL = _podman_contract._ATTEMPT_LABEL
_PRINCIPAL_LABEL = _podman_contract._PRINCIPAL_LABEL
_POLICY_LABEL = _podman_contract._POLICY_LABEL
_SPECIFICATION_LABEL = _podman_contract._SPECIFICATION_LABEL
_DEADLINE_LABEL = _podman_contract._DEADLINE_LABEL
_IMAGE_DIGEST_LABEL = _podman_contract._IMAGE_DIGEST_LABEL
_IMAGE_REPOSITORY_LABEL = _podman_contract._IMAGE_REPOSITORY_LABEL
_RUN_AS_UID_LABEL = _podman_contract._RUN_AS_UID_LABEL
_CPU_QUOTA_LABEL = _podman_contract._CPU_QUOTA_LABEL
_CPU_PERIOD_LABEL = _podman_contract._CPU_PERIOD_LABEL
_MEMORY_LABEL = _podman_contract._MEMORY_LABEL
_PID_LIMIT_LABEL = _podman_contract._PID_LIMIT_LABEL
_WORKSPACE_LABEL = _podman_contract._WORKSPACE_LABEL
_WALL_TIME_LABEL = _podman_contract._WALL_TIME_LABEL
_MAX_INPUT_LABEL = _podman_contract._MAX_INPUT_LABEL
_MAX_OUTPUT_LABEL = _podman_contract._MAX_OUTPUT_LABEL
_MANAGED_LABEL_KEYS = _podman_contract._MANAGED_LABEL_KEYS
_IMAGE_REPOSITORY_PATTERN = _podman_contract._IMAGE_REPOSITORY_PATTERN
_CONTAINER_ID_PATTERN = _podman_contract._CONTAINER_ID_PATTERN
_CONTAINER_ID_LENGTH = _podman_contract._CONTAINER_ID_LENGTH
_TIMESTAMP_PATTERN = _podman_contract._TIMESTAMP_PATTERN
_FIXED_ENTRYPOINT = _podman_contract._FIXED_ENTRYPOINT
_FIXED_ENVIRONMENT = _podman_contract._FIXED_ENVIRONMENT
_INSPECT_MAX_BYTES = _podman_contract._INSPECT_MAX_BYTES
_MAX_COMMAND_OUTPUT_BYTES = _podman_contract._MAX_COMMAND_OUTPUT_BYTES
_MIN_OUTPUT_BYTES = _podman_contract._MIN_OUTPUT_BYTES
_MAX_LABEL_BYTES = _podman_contract._MAX_LABEL_BYTES
_CGROUP_EVENTS_MAX_BYTES = _podman_cgroups._CGROUP_EVENTS_MAX_BYTES
_SYSTEMD_PROPERTIES_MAX_BYTES = _podman_cgroups._SYSTEMD_PROPERTIES_MAX_BYTES
_OWNER_ONLY_MODE = _podman_cgroups._OWNER_ONLY_MODE
PodmanRuntimeUnit = _podman_contract.PodmanRuntimeUnit
Command = _podman_contract.Command
SystemdCgroupRemover = _podman_cgroups.SystemdCgroupRemover
_container_name = _podman_contract._container_name
_cgroup_parent = _podman_contract._cgroup_parent
_read_cgroup_events = _podman_cgroups._read_cgroup_events
_create_cgroup = _podman_cgroups._create_cgroup
_validate_cgroup_root = _podman_cgroups._validate_cgroup_root
_read_process_cgroup = _podman_cgroups._read_process_cgroup
_validate_hooks_directory = _podman_cgroups._validate_hooks_directory
_parse_cgroup_events = _podman_cgroups._parse_cgroup_events
_parse_systemd_properties = _podman_cgroups._parse_systemd_properties
_labels = _podman_contract._labels
_policy_from_labels = _podman_contract._policy_from_labels
_json = _podman_contract._json
_matches_create_command = _podman_contract._matches_create_command
_matches_entrypoint = _podman_contract._matches_entrypoint
_mapping = _podman_contract._mapping
_create_response_identity = _podman_contract._create_response_identity
_matches_process_cgroup = _podman_cgroups._matches_process_cgroup
_string = _podman_contract._string
_boolean = _podman_contract._boolean
_integer = _podman_contract._integer
_timestamp = _podman_contract._timestamp
_label = _podman_contract._label
_positive_integer_label = _podman_contract._positive_integer_label
_evidence = _podman_contract._evidence
build_create_arguments = _podman_contract.build_create_arguments
verify_realized_specification = _podman_contract.verify_realized_specification

_build_request_archive = _podman_workspace._build_request_archive

_read_single_file_archive = _podman_workspace._read_single_file_archive

_tar_output_ceiling = _podman_workspace._tar_output_ceiling

BoundedCommandRunner = _command_runner.BoundedCommandRunner

PodmanCommandLimits = _command_runner.PodmanCommandLimits


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

    def prepare(
        self, unit: ManagedUnit, policy: BrokerPolicy
    ) -> RuntimeRecoveryBinding | None:
        """Require no prepared binding for label-recoverable Podman."""

        del unit, policy
        return None

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
        created_id: str | None = None
        inspected: Mapping[str, Any] | None = None
        try:
            created_id = _create_response_identity(output)
            inspected = self._inspect(name)
            runtime_unit = self._verified_unit(inspected, expected=unit, policy=policy)
            if (
                create_code == 0
                and (created_id is None or created_id != runtime_unit.container_id)
            ) or (create_code != 0 and output != b""):
                raise PodmanRuntimeError("Podman create identity is invalid")
            state = _mapping(inspected, "State")
            status = _string(state, "Status")
            if status == "created":
                self._call(("start", runtime_unit.container_id), max_output_bytes=1024)
                runtime_unit = self._verified_unit(
                    self._inspect(runtime_unit.container_id),
                    expected=unit,
                    policy=policy,
                )
                status = _string(
                    _mapping(self._inspect(runtime_unit.container_id), "State"),
                    "Status",
                )
            if status not in {"running", "exited", "stopped"}:
                raise PodmanRuntimeError("Podman container state is invalid")
            return runtime_unit
        except BaseException as create_error:
            if create_code == 0:
                if (
                    created_id is None
                    or _CONTAINER_ID_PATTERN.fullmatch(created_id) is None
                ):
                    raise PodmanRuntimeError(
                        "Podman failed creation cleanup is unconfirmed"
                    ) from create_error
                self._cleanup_failed_create(unit.unit_id, created_id)
            elif inspected is None:
                self._cleanup_absent_failed_create(unit.unit_id, name)
            raise

    def stage_request(
        self, runtime_unit: RuntimeUnit, request: ReverseAttemptRequest
    ) -> None:
        """Copy one broker-authored request archive and publish its final marker."""

        verified = self._coerce_unit(runtime_unit)
        inspected, policy = self._workspace_context(verified)
        labels = _mapping(_mapping(inspected, "Config"), "Labels")
        if (
            type(request) is not ReverseAttemptRequest
            or str(request.attempt_id) != _label(labels, _ATTEMPT_LABEL)
            or len(request.source) > policy.channel_limits.max_input_bytes
            or request.limits.max_input_bytes > policy.channel_limits.max_input_bytes
            or request.limits.max_output_bytes > policy.channel_limits.max_output_bytes
        ):
            raise PodmanRuntimeError("Podman workspace request is invalid")
        archive = _build_request_archive(request)
        try:
            self._call(
                ("cp", "-", f"{verified.container_id}:/work"),
                max_output_bytes=0,
                input_bytes=archive,
            )
        except BaseException:
            self._workspace_context(verified)
            raise
        self._workspace_context(verified)

    def try_collect_response(
        self, runtime_unit: RuntimeUnit, expected_attempt_id: UUID
    ) -> ReverseAttemptResponse | None:
        """Copy and validate an exact committed response from the live tmpfs."""

        verified = self._coerce_unit(runtime_unit)
        inspected, policy = self._workspace_context(verified)
        labels = _mapping(_mapping(inspected, "Config"), "Labels")
        if type(expected_attempt_id) is not UUID or str(expected_attempt_id) != _label(
            labels, _ATTEMPT_LABEL
        ):
            raise PodmanRuntimeError("Podman workspace response identity is invalid")
        try:
            state = self._copy_workspace_file(
                verified, "response.state", MAX_METADATA_BYTES
            )
            channel_state = decode_channel_state(state, expected_attempt_id)
            if channel_state == "pending":
                response: ReverseAttemptResponse | None = None
            else:
                metadata = self._copy_workspace_file(
                    verified, "response.json", MAX_METADATA_BYTES
                )
                try:
                    shape = json.loads(metadata.decode("ascii"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise PodmanRuntimeError(
                        "Podman workspace response is invalid"
                    ) from error
                result = (
                    self._copy_workspace_file(
                        verified,
                        "result.bin",
                        policy.channel_limits.max_output_bytes,
                    )
                    if isinstance(shape, Mapping) and shape.get("type") == "success"
                    else None
                )
                response = decode_response_metadata(metadata, result)
        except ReverseConversionError as error:
            raise PodmanRuntimeError("Podman workspace response is invalid") from error
        return response

    def _copy_workspace_file(
        self, runtime_unit: PodmanRuntimeUnit, leaf: str, maximum: int
    ) -> bytes:
        try:
            _, output = self._call(
                ("cp", f"{runtime_unit.container_id}:/work/{leaf}", "-"),
                max_output_bytes=_tar_output_ceiling(maximum),
            )
            content = _read_single_file_archive(output, leaf, maximum)
        except BaseException:
            self._workspace_context(runtime_unit)
            raise
        self._workspace_context(runtime_unit)
        return content

    def _workspace_context(
        self, runtime_unit: PodmanRuntimeUnit
    ) -> tuple[Mapping[str, Any], BrokerPolicy]:
        self._require_rootless_environment()
        inspected = self._inspect(runtime_unit.container_id)
        self._verify_incarnation(inspected, runtime_unit)
        state = _mapping(inspected, "State")
        if not _boolean(state, "Running") or _string(state, "Status") != "running":
            raise PodmanRuntimeError("Podman workspace unit is not running")
        labels = _mapping(_mapping(inspected, "Config"), "Labels")
        return inspected, _policy_from_labels(labels)

    def _cleanup_failed_create(self, unit_id: UUID, container_id: str) -> None:
        with suppress(BaseException):
            self._call(
                ("rm", "--force", container_id),
                max_output_bytes=1024,
                accepted_exit_codes=frozenset({0, 1}),
            )
        if not self._confirm_container_absence(unit_id, container_id):
            raise PodmanRuntimeError("Podman failed creation cleanup is unconfirmed")
        self._remove_failed_create_cgroup(unit_id)

    def _cleanup_absent_failed_create(self, unit_id: UUID, name: str) -> None:
        if not self._confirm_container_absence(unit_id, name):
            raise PodmanRuntimeError("Podman failed creation cleanup is unconfirmed")
        self._remove_failed_create_cgroup(unit_id)

    def _confirm_container_absence(self, unit_id: UUID, identity: str) -> bool:
        try:
            exists_code, _ = self._call(
                ("container", "exists", identity),
                max_output_bytes=0,
                accepted_exit_codes=frozenset({0, 1}),
            )
            _, discovery_output = self._call(
                (
                    "ps",
                    "--all",
                    "--no-trunc",
                    "--filter",
                    f"label={_UNIT_LABEL}={unit_id}",
                    "--format",
                    "json",
                ),
                max_output_bytes=_INSPECT_MAX_BYTES,
            )
            discovered = _json(discovery_output)
            return exists_code == 1 and type(discovered) is list and not discovered
        except BaseException:
            return False

    def _remove_failed_create_cgroup(self, unit_id: UUID) -> None:
        try:
            self._cgroup_remove(self._cgroup_path(unit_id))
        except BaseException as error:
            raise PodmanRuntimeError(
                "Podman failed creation cleanup is unconfirmed"
            ) from error

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
        return build_create_arguments(
            self._image_repository, self._run_as_uid, name, labels, policy
        )

    def _cgroup_path(self, unit_id: UUID) -> Path:
        parent = _cgroup_parent(_container_name(unit_id))
        return self._cgroup_root / parent

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
        input_bytes: bytes | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        if input_bytes is None:
            return self._command(
                self._podman_arguments(arguments),
                max_output_bytes=max_output_bytes,
                accepted_exit_codes=accepted_exit_codes,
            )
        return self._command(
            self._podman_arguments(arguments),
            max_output_bytes=max_output_bytes,
            input_bytes=input_bytes,
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

    def recover(
        self, unit: ManagedUnit, binding: RuntimeRecoveryBinding
    ) -> PodmanRuntimeUnit:
        """Reject recovery material because Podman reconstructs from fixed labels."""

        del unit, binding
        raise PodmanRuntimeError("Podman recovery binding is invalid")

    def recover_create_intent(
        self, unit: ManagedUnit, binding: RuntimeRecoveryBinding
    ) -> PodmanRuntimeUnit | None:
        """Reject bindings because Podman recovers create intent from labels."""

        del unit, binding
        raise PodmanRuntimeError("Podman recovery binding is invalid")

    def acknowledge_recovery(
        self, unit: ManagedUnit, binding: RuntimeRecoveryBinding
    ) -> None:
        """Reject recovery material because Podman retains no recovery ledger."""

        del unit, binding
        raise PodmanRuntimeError("Podman recovery binding is invalid")

    def _unit_from_labels(self, inspected: Mapping[str, Any]) -> PodmanRuntimeUnit:
        container_id = _string(inspected, "Id")
        if _CONTAINER_ID_PATTERN.fullmatch(container_id) is None:
            raise PodmanRuntimeError("Podman container identity is invalid")
        labels = _mapping(_mapping(inspected, "Config"), "Labels")
        try:
            unit_id = UUID(_label(labels, _UNIT_LABEL))
            attempt_id = UUID(_label(labels, _ATTEMPT_LABEL))
            principal_id = UUID(_label(labels, _PRINCIPAL_LABEL))
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
            attempt_id,
            principal_id,
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
            if pid <= 0 or not _matches_process_cgroup(
                self._process_cgroup_read(pid), expected
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
        verify_realized_specification(
            self._run_as_uid, self._runtime_capabilities, inspected, policy
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
            or actual.attempt_id != expected.attempt_id
            or actual.principal_id != expected.principal_id
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
        try:
            unit_id = runtime_unit.unit_id
            attempt_id = runtime_unit.attempt_id
            principal_id = runtime_unit.principal_id
            incarnation = runtime_unit.incarnation
        except AttributeError, TypeError:
            raise PodmanRuntimeError("Podman runtime identity is invalid") from None
        if (
            allow_stored
            and type(unit_id) is UUID
            and type(attempt_id) is UUID
            and type(principal_id) is UUID
            and type(incarnation) is RuntimeIncarnation
        ):
            # The deterministic name and persisted incarnation allow post-removal event recovery.
            return PodmanRuntimeUnit(
                unit_id,
                attempt_id,
                principal_id,
                incarnation,
                "0" * 64,
                _container_name(unit_id),
            )
        raise PodmanRuntimeError("Podman runtime identity is invalid")
