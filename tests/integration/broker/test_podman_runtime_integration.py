from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from uuid import UUID

import pytest

from markweave.broker import podman_runtime
from markweave.broker.errors import BrokerError
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    ManagedUnitState,
    ReplayPosition,
    RuntimeChannelLimits,
    RuntimeLimits,
)
from markweave.broker.podman_runtime import (
    BoundedCommandRunner,
    PodmanCommandLimits,
    PodmanIsolationRuntime,
    PodmanRuntimeError,
    SystemdCgroupRemover,
)
from markweave.broker.service import IsolationBrokerService
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptRequest,
    ReverseContentLimits,
)

ROOT = Path(__file__).parents[3]
PODMAN = Path("/usr/bin/podman")
SYSTEMCTL = Path("/usr/bin/systemctl")
TEST_IMAGE = "localhost/markweave-t70-runtime-integration:current"
WORKSPACE_IMAGE = "localhost/markweave-t70-workspace-integration:current"
DEFAULT_BASE_IMAGE = "localhost/markweave-reverse-attempt:t70-runtime-integration"
PRINCIPAL = AuthenticatedPrincipal(UUID("33333333-3333-4333-8333-333333333333"))
UNIT_IDS = (
    UUID("44444444-4444-4444-8444-444444444441"),
    UUID("44444444-4444-4444-8444-444444444442"),
    UUID("44444444-4444-4444-8444-444444444443"),
    UUID("44444444-4444-4444-8444-444444444444"),
    UUID("44444444-4444-4444-8444-444444444445"),
    UUID("44444444-4444-4444-8444-444444444446"),
)


def _cgroup_path(unit_id: UUID) -> Path:
    return Path(
        f"/sys/fs/cgroup/user.slice/user-{os.geteuid()}.slice/"
        f"user@{os.geteuid()}.service/markweavet70{unit_id.hex}.slice"
    )


def _systemd_slice_name(unit_id: UUID) -> str:
    return f"markweavet70{unit_id.hex}.slice"


def _podman(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("CONTAINER_HOST", None)
    return subprocess.run(
        (str(PODMAN), *arguments),
        check=check,
        capture_output=True,
        text=True,
        timeout=120,
        env=environment,
    )


def _systemctl(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("CONTAINER_HOST", None)
    return subprocess.run(
        (str(SYSTEMCTL), *arguments),
        check=check,
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )


def _assert_systemd_slice_clean(unit_id: UUID) -> None:
    name = _systemd_slice_name(unit_id)
    properties = dict(
        line.split("=", 1)
        for line in _systemctl(
            "--user",
            "show",
            name,
            "--property=LoadState",
            "--property=ActiveState",
            "--property=SubState",
            "--property=ControlGroup",
        ).stdout.splitlines()
    )
    assert properties == {
        "ActiveState": "inactive",
        "ControlGroup": "",
        "LoadState": "loaded",
        "SubState": "dead",
    }
    assert not _cgroup_path(unit_id).exists()
    for arguments in (
        ("--type=slice", "--state=active"),
        ("--type=slice", "--all"),
    ):
        listing = _systemctl(
            "--user",
            "list-units",
            *arguments,
            "--plain",
            "--no-legend",
            "--no-pager",
        ).stdout.splitlines()
        assert all(line.split(maxsplit=1)[0] != name for line in listing if line)


def _cleanup_systemd_slice(unit_id: UUID) -> None:
    _systemctl("--user", "stop", _systemd_slice_name(unit_id))
    _assert_systemd_slice_clean(unit_id)


@pytest.fixture(scope="module")
def controlled_image() -> Iterator[tuple[str, str]]:
    for unit_id in UNIT_IDS:
        _cleanup_systemd_slice(unit_id)
    base = os.environ.get("MARKWEAVE_T70_PODMAN_TEST_IMAGE", DEFAULT_BASE_IMAGE)
    built_base = base == DEFAULT_BASE_IMAGE
    if built_base:
        subprocess.run(
            ("bash", "scripts/container/build-reverse-attempt.sh", base),
            check=True,
            cwd=ROOT,
            timeout=600,
        )
    _podman(
        "build",
        "--format",
        "oci",
        "--tag",
        TEST_IMAGE,
        "--file",
        str(ROOT / "tests/integration/broker/fixtures/Containerfile"),
        "--build-arg",
        f"BASE_IMAGE={base}",
        str(ROOT / "tests/integration/broker/fixtures"),
    )
    inspected = json.loads(
        _podman("image", "inspect", TEST_IMAGE, "--format", "json").stdout
    )[0]
    digest = inspected["Digest"]
    assert isinstance(digest, str) and digest.startswith("sha256:")
    try:
        yield TEST_IMAGE.rsplit(":", 1)[0], digest
    finally:
        for unit_id in UNIT_IDS:
            _podman(
                "rm",
                "--force",
                f"markweave-reverse-{unit_id.hex}",
                check=False,
            )
            _cleanup_systemd_slice(unit_id)
        _podman("image", "rm", "--force", TEST_IMAGE)
        if built_base:
            _podman("image", "rm", "--force", base)


@pytest.fixture(scope="module")
def workspace_image(
    controlled_image: tuple[str, str],
) -> Iterator[tuple[str, str]]:
    del controlled_image
    base = os.environ.get("MARKWEAVE_T70_PODMAN_TEST_IMAGE", DEFAULT_BASE_IMAGE)
    _podman(
        "build",
        "--format",
        "oci",
        "--tag",
        WORKSPACE_IMAGE,
        "--file",
        str(ROOT / "tests/integration/broker/fixtures/WorkspaceContainerfile"),
        "--build-arg",
        f"BASE_IMAGE={base}",
        str(ROOT),
    )
    inspected = json.loads(
        _podman("image", "inspect", WORKSPACE_IMAGE, "--format", "json").stdout
    )[0]
    digest = inspected["Digest"]
    assert isinstance(digest, str) and digest.startswith("sha256:")
    try:
        yield WORKSPACE_IMAGE.rsplit(":", 1)[0], digest
    finally:
        _podman("image", "rm", "--force", WORKSPACE_IMAGE)


def _policy(image_digest: str, wall_time_millis: int = 10_000) -> BrokerPolicy:
    return BrokerPolicy(
        "integration",
        image_digest,
        RuntimeLimits(100_000, 100_000, 268_435_456, 16, 8_388_608, wall_time_millis),
        RuntimeChannelLimits(1_000_000, 2_000_000),
    )


def _request(attempt_id: UUID) -> ReverseAttemptRequest:
    source = (ROOT / "spikes/anydoc/corpus/pdf/text.pdf").read_bytes()
    return ReverseAttemptRequest(
        attempt_id,
        ".pdf",
        ReverseContentLimits(
            1_000_000,
            2_000_000,
            100_000,
            1_000,
            1_000,
            1_000_000,
            1_000,
            32,
            16,
            500_000,
            1_000_000,
            1_000_000,
            2_000_000,
        ),
        source,
    )


@pytest.mark.integration
def test_real_rootless_podman_bounded_workspace_round_trip(
    tmp_path: Path, workspace_image: tuple[str, str]
) -> None:
    repository, digest = workspace_image
    service, inventory, runtime = _service(
        tmp_path, repository, _policy(digest), UNIT_IDS[4]
    )
    attempt_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    created = service.create(ReplayPosition(PRINCIPAL, 1), attempt_id)
    runtime_unit = runtime.discover(limit=1)[0]
    request = _request(attempt_id)

    runtime.stage_request(runtime_unit, request)
    staged_state = json.loads(
        _podman(
            "container", "inspect", runtime_unit.container_id, "--format", "json"
        ).stdout
    )[0]["State"]
    assert staged_state["Running"] is True, json.dumps(staged_state, sort_keys=True)
    deadline = time.monotonic() + 10
    response = None
    while response is None and time.monotonic() < deadline:
        response = runtime.try_collect_response(runtime_unit, attempt_id)
        if response is None:
            time.sleep(0.05)

    assert response is not None
    assert not isinstance(response, ReverseAttemptFailure), (
        response.category if isinstance(response, ReverseAttemptFailure) else None
    )
    inspected = json.loads(
        _podman(
            "container", "inspect", runtime_unit.container_id, "--format", "json"
        ).stdout
    )[0]
    assert inspected["State"]["Running"] is True
    assert "MARKWEAVE_REVERSE_MAX_INPUT_BYTES=1000000" in inspected["Config"]["Env"]
    assert "MARKWEAVE_REVERSE_MAX_OUTPUT_BYTES=2000000" in inspected["Config"]["Env"]
    serialized = json.dumps(inspected["Config"]["Labels"], sort_keys=True).encode()
    assert request.source[:32] not in serialized
    for inventory_file in tmp_path.glob("inventory.sqlite3*"):
        assert request.source[:32] not in inventory_file.read_bytes()
    logs = _podman("logs", runtime_unit.container_id, check=False)
    assert logs.stdout == ""
    assert request.source[:32].hex() not in logs.stderr

    proof = service.terminate(PRINCIPAL, attempt_id, created.unit_id)
    assert proof.unit_id == created.unit_id
    retained = inventory.get(created.unit_id)
    assert retained is not None and retained.state is ManagedUnitState.REMOVED
    _assert_systemd_slice_clean(created.unit_id)


@pytest.mark.integration
def test_staged_workspace_is_swept_after_broker_crash(
    tmp_path: Path, workspace_image: tuple[str, str]
) -> None:
    repository, digest = workspace_image
    policy = _policy(digest, 2_001)
    service, inventory, runtime = _service(tmp_path, repository, policy, UNIT_IDS[5])
    attempt_id = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
    created = service.create(ReplayPosition(PRINCIPAL, 1), attempt_id)
    runtime_unit = runtime.discover(limit=1)[0]
    runtime.stage_request(runtime_unit, _request(attempt_id))
    del service
    del inventory

    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        inspected = json.loads(
            _podman(
                "container", "inspect", runtime_unit.container_id, "--format", "json"
            ).stdout
        )[0]
        if inspected["State"]["Status"] in {"exited", "stopped"}:
            break
        time.sleep(0.1)
    assert inspected["State"]["Status"] in {"exited", "stopped"}

    restarted_inventory = SQLiteBrokerInventory(
        tmp_path / "inventory.sqlite3", b"i" * 32, max_records=8
    )
    restarted = IsolationBrokerService(
        restarted_inventory,
        _runtime(repository, tmp_path / "hooks"),
        policy,
        max_discovered_units=8,
    )
    restarted.start()
    retained = restarted_inventory.get(created.unit_id)
    assert retained is not None and retained.state is ManagedUnitState.REMOVED
    _assert_systemd_slice_clean(created.unit_id)


def _runtime(
    repository: str,
    hooks_directory: Path,
    command: Callable[..., tuple[int, bytes]] | None = None,
) -> PodmanIsolationRuntime:
    environment = {
        key: os.environ[key]
        for key in ("DBUS_SESSION_BUS_ADDRESS", "HOME", "XDG_RUNTIME_DIR")
        if key in os.environ
    }
    environment.update({"CONTAINERS_CONF": "/dev/null", "PATH": "/usr/bin:/bin"})
    systemd_environment = {
        key: value for key, value in environment.items() if key != "CONTAINERS_CONF"
    }
    return PodmanIsolationRuntime(
        image_repository=repository,
        run_as_uid=1001,
        command=command
        or BoundedCommandRunner(
            PODMAN, PodmanCommandLimits(20), environment=environment
        ),
        cgroup_root=Path(
            f"/sys/fs/cgroup/user.slice/user-{os.geteuid()}.slice/"
            f"user@{os.geteuid()}.service"
        ),
        hooks_directory=hooks_directory,
        cgroup_remove=SystemdCgroupRemover(
            BoundedCommandRunner(
                SYSTEMCTL,
                PodmanCommandLimits(20),
                environment=systemd_environment,
            )
        ),
    )


def _service(
    tmp_path: Path, repository: str, policy: BrokerPolicy, unit_id: UUID
) -> tuple[IsolationBrokerService, SQLiteBrokerInventory, PodmanIsolationRuntime]:
    inventory = SQLiteBrokerInventory(
        tmp_path / "inventory.sqlite3", b"i" * 32, max_records=8
    )
    hooks = tmp_path / "hooks"
    hooks.mkdir(mode=0o700, exist_ok=True)
    runtime = _runtime(repository, hooks)
    service = IsolationBrokerService(
        inventory,
        runtime,
        policy,
        max_discovered_units=8,
        unit_id_factory=lambda: unit_id,
    )
    service.start()
    return service, inventory, runtime


@pytest.mark.integration
def test_real_rootless_podman_whole_unit_lifecycle_and_proof(
    tmp_path: Path,
    controlled_image: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        _podman("info", "--format", "{{.Host.Security.Rootless}}").stdout.strip()
        == "true"
    )
    monkeypatch.setenv("CONTAINER_HOST", "tcp://127.0.0.1:1")
    repository, digest = controlled_image
    service, inventory, runtime = _service(
        tmp_path, repository, _policy(digest), UNIT_IDS[0]
    )
    attempt_id = UUID("11111111-1111-4111-8111-111111111111")
    created = service.create(ReplayPosition(PRINCIPAL, 1), attempt_id)
    runtime_unit = runtime.discover(limit=1)[0]

    inspected = json.loads(
        _podman(
            "container",
            "inspect",
            runtime_unit.container_id,
            "--format",
            "json",
        ).stdout
    )[0]
    assert inspected["HostConfig"]["NetworkMode"] == "none"
    assert inspected["HostConfig"]["ReadonlyRootfs"] is True
    assert inspected["HostConfig"]["Privileged"] is False
    assert inspected["HostConfig"]["IpcMode"] == "none"
    assert inspected["HostConfig"]["LogConfig"]["Type"] == "none"
    assert inspected["HostConfig"]["RestartPolicy"]["Name"] == "no"
    assert inspected["HostConfig"]["PidsLimit"] == 16
    assert inspected["HostConfig"]["Memory"] == 268_435_456
    assert inspected["HostConfig"]["MemorySwap"] == 268_435_456
    assert inspected["HostConfig"]["Tmpfs"] == {
        "/work": "mode=0770,size=8388608,rw,rprivate,nosuid,nodev,tmpcopyup"
    }
    assert (
        _podman(
            "exec", inspected["Id"], "/usr/bin/stat", "-c", "%u:%g", "/work/ready"
        ).stdout.strip()
        == "1001:0"
    )
    assert len(_podman("top", inspected["Id"], "hpid").stdout.splitlines()) == 3
    process_cgroup = Path(f"/proc/{inspected['State']['Pid']}/cgroup").read_bytes()
    assert podman_runtime._matches_process_cgroup(
        process_cgroup, inspected["State"]["CgroupPath"]
    )
    actual_scope = Path("/sys/fs/cgroup") / inspected["State"]["CgroupPath"].lstrip("/")
    assert actual_scope.parent == _cgroup_path(created.unit_id)
    assert (_cgroup_path(created.unit_id) / "cgroup.events").read_text(
        encoding="ascii"
    ) == "populated 1\nfrozen 0\n"

    runtime.hard_terminate(runtime_unit)
    assert (_cgroup_path(created.unit_id) / "cgroup.events").read_text(
        encoding="ascii"
    ) == "populated 0\nfrozen 0\n"

    proof = service.terminate(PRINCIPAL, attempt_id, created.unit_id)

    stored = inventory.get(created.unit_id)
    assert stored is not None and stored.state is ManagedUnitState.REMOVED
    assert proof.unit_id == created.unit_id
    assert runtime.discover(limit=1) == ()
    _assert_systemd_slice_clean(created.unit_id)


@pytest.mark.integration
def test_runtime_deadline_survives_broker_and_inventory_shutdown(
    tmp_path: Path, controlled_image: tuple[str, str]
) -> None:
    repository, digest = controlled_image
    service, inventory, runtime = _service(
        tmp_path, repository, _policy(digest, 1_001), UNIT_IDS[1]
    )
    attempt_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    created = service.create(ReplayPosition(PRINCIPAL, 1), attempt_id)
    runtime_unit = runtime.discover(limit=1)[0]
    del service
    del inventory

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        inspected = json.loads(
            _podman(
                "container", "inspect", runtime_unit.container_id, "--format", "json"
            ).stdout
        )[0]
        if inspected["State"]["Status"] == "exited":
            break
        time.sleep(0.1)
    assert inspected["State"]["Status"] == "exited"
    assert inspected["Config"]["Timeout"] == 2

    restarted_inventory = SQLiteBrokerInventory(
        tmp_path / "inventory.sqlite3", b"i" * 32, max_records=8
    )
    restarted = IsolationBrokerService(
        restarted_inventory,
        _runtime(repository, tmp_path / "hooks"),
        _policy(digest, 1_001),
        max_discovered_units=8,
    )
    restarted.start()
    assert restarted.ready is True
    retained = restarted_inventory.get(created.unit_id)
    assert retained is not None and retained.state is ManagedUnitState.REMOVED
    _assert_systemd_slice_clean(created.unit_id)


@pytest.mark.integration
def test_orphan_restart_sweep_refuses_readiness_on_identity_mismatch(
    tmp_path: Path, controlled_image: tuple[str, str]
) -> None:
    repository, digest = controlled_image
    service, inventory, runtime = _service(
        tmp_path, repository, _policy(digest), UNIT_IDS[2]
    )
    attempt_id = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    service.create(ReplayPosition(PRINCIPAL, 1), attempt_id)
    runtime_unit = runtime.discover(limit=1)[0]
    del service
    del inventory
    # Podman labels are immutable, so substitution is represented by removing the exact
    # incarnation and creating a same-name unmanaged container. Discovery must not hide it as proof.
    _podman("rm", "--force", runtime_unit.container_id)
    _podman(
        "create",
        "--name",
        runtime_unit.name,
        "--entrypoint",
        "/bin/true",
        f"{repository}@{digest}",
    )
    restarted_inventory = SQLiteBrokerInventory(
        tmp_path / "inventory.sqlite3", b"i" * 32, max_records=8
    )
    restarted = IsolationBrokerService(
        restarted_inventory,
        _runtime(repository, tmp_path / "hooks"),
        _policy(digest),
        max_discovered_units=8,
    )
    with pytest.raises(BrokerError):
        restarted.start()
    assert restarted.ready is False
    _podman("rm", "--force", runtime_unit.name)
    # This scenario deliberately bypasses broker cleanup after identity substitution.
    _cleanup_systemd_slice(UNIT_IDS[2])


@pytest.mark.integration
def test_persisted_empty_proof_reconstructs_removal_without_event_history(
    tmp_path: Path, controlled_image: tuple[str, str]
) -> None:
    repository, digest = controlled_image
    hooks = tmp_path / "hooks"
    hooks.mkdir(mode=0o700, exist_ok=True)
    environment = {
        key: os.environ[key]
        for key in ("DBUS_SESSION_BUS_ADDRESS", "HOME", "XDG_RUNTIME_DIR")
        if key in os.environ
    }
    environment.update({"CONTAINERS_CONF": "/dev/null", "PATH": "/usr/bin:/bin"})
    command = BoundedCommandRunner(
        PODMAN, PodmanCommandLimits(20), environment=environment
    )
    fail_absence_query = False
    removed = False

    def interrupted(
        arguments: Sequence[str],
        *,
        max_output_bytes: int | None = None,
        accepted_exit_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[int, bytes]:
        nonlocal removed
        if fail_absence_query and removed and arguments[3] == "ps":
            raise PodmanRuntimeError("Injected lost Podman response")
        result = command(
            arguments,
            max_output_bytes=max_output_bytes,
            accepted_exit_codes=accepted_exit_codes,
        )
        if arguments[3] == "rm":
            removed = True
        return result

    policy = _policy(digest)
    inventory = SQLiteBrokerInventory(
        tmp_path / "inventory.sqlite3", b"i" * 32, max_records=8
    )
    service = IsolationBrokerService(
        inventory,
        _runtime(repository, hooks, interrupted),
        policy,
        max_discovered_units=8,
        unit_id_factory=lambda: UNIT_IDS[3],
    )
    service.start()
    attempt_id = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
    created = service.create(ReplayPosition(PRINCIPAL, 1), attempt_id)
    fail_absence_query = True

    with pytest.raises(BrokerError):
        service.terminate(PRINCIPAL, attempt_id, created.unit_id)

    interrupted_unit = inventory.get(created.unit_id)
    assert (
        interrupted_unit is not None
        and interrupted_unit.state is ManagedUnitState.EMPTY_CONFIRMED
    )
    restarted = IsolationBrokerService(
        inventory, _runtime(repository, hooks), policy, max_discovered_units=8
    )
    restarted.start()
    reconstructed = inventory.get(created.unit_id)
    assert reconstructed is not None and reconstructed.state is ManagedUnitState.REMOVED
    _assert_systemd_slice_clean(created.unit_id)
