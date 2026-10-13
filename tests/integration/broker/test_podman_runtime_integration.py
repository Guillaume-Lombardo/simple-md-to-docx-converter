from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pytest_mock import MockerFixture

from markweave.broker import podman_runtime
from markweave.broker.dispatch import BrokerDispatcher
from markweave.broker.errors import BrokerError, BrokerErrorCategory
from markweave.broker.inventory import SQLiteBrokerInventory
from markweave.broker.models import (
    AuthenticatedPrincipal,
    BrokerPolicy,
    ManagedUnitState,
    ReplayPosition,
    RuntimeChannelLimits,
    RuntimeLimits,
)
from markweave.broker.mtls_transport import (
    MtlsBrokerClient,
    MtlsBrokerServer,
    MtlsEndpoint,
    MtlsLocalIdentity,
    MtlsPeerIdentity,
    MtlsTransportLimits,
)
from markweave.broker.podman_runtime import (
    BoundedCommandRunner,
    CgroupEmptinessWait,
    PodmanCommandLimits,
    PodmanIsolationRuntime,
    PodmanRuntimeError,
    SystemdCgroupRemover,
)
from markweave.broker.process import podman_emptiness_wait
from markweave.broker.protocol import (
    BrokerOperation,
    CreateRequest,
    CreateResponse,
    ErrorResponse,
    ReadyRequest,
    ReadyResponse,
    TerminateRequest,
)
from markweave.broker.reconciliation_protocol import (
    ReconciliationRequest,
    ReconciliationResponse,
)
from markweave.broker.service import IsolationBrokerService
from markweave.broker.unix_transport import (
    UnixBrokerClient,
    UnixBrokerServer,
    UnixTransportLimits,
)
from markweave.reversions.models import (
    ReverseAttemptFailure,
    ReverseAttemptRequest,
    ReverseContentLimits,
)
from tests.integration.broker.cgroup_events import await_cgroup_events
from tests.integration.broker.slow_reaping import install_slow_reaping
from tests.integration.broker.test_mtls_transport_integration import (
    CLIENT_URI,
    SERVER_URI,
    _ca,
    _issue,
    _pin,
)

pytestmark = pytest.mark.requires_podman

ROOT = Path(__file__).parents[3]
PODMAN = Path("/usr/bin/podman")
SYSTEMCTL = Path("/usr/bin/systemctl")
SYSTEMD_RUN = Path("/usr/bin/systemd-run")
EMPTY_CGROUP_EVENTS = "populated 0\nfrozen 0\n"
POPULATED_CGROUP_EVENTS = "populated 1\nfrozen 0\n"
TEST_IMAGE = "localhost/markweave-t70-runtime-integration:current"
WORKSPACE_IMAGE = "localhost/markweave-t70-workspace-integration:current"
INCOMPATIBLE_BASE_IMAGE = "localhost/markweave-t70-incompatible-base:current"
REJECTED_WORKSPACE_IMAGE = "localhost/markweave-t70-rejected-workspace:current"
MISSING_DEPENDENCY_BASE_IMAGE = (
    "localhost/markweave-t70-missing-dependency-base:current"
)
REJECTED_DEPENDENCY_WORKSPACE_IMAGE = (
    "localhost/markweave-t70-rejected-dependency-workspace:current"
)
DEFAULT_BASE_IMAGE = "localhost/markweave-reverse-attempt:t70-runtime-integration"
PRINCIPAL = AuthenticatedPrincipal(UUID("33333333-3333-4333-8333-333333333333"))
UNIT_IDS = (
    UUID("44444444-4444-4444-8444-444444444441"),
    UUID("44444444-4444-4444-8444-444444444442"),
    UUID("44444444-4444-4444-8444-444444444443"),
    UUID("44444444-4444-4444-8444-444444444444"),
    UUID("44444444-4444-4444-8444-444444444445"),
    UUID("44444444-4444-4444-8444-444444444446"),
    UUID("44444444-4444-4444-8444-444444444447"),
    UUID("44444444-4444-4444-8444-444444444448"),
    UUID("44444444-4444-4444-8444-444444444449"),
    UUID("44444444-4444-4444-8444-44444444444a"),
    UUID("44444444-4444-4444-8444-44444444444b"),
    UUID("44444444-4444-4444-8444-44444444444c"),
    UUID("44444444-4444-4444-8444-44444444444d"),
    UUID("44444444-4444-4444-8444-44444444444e"),
    UUID("44444444-4444-4444-8444-44444444444f"),
    UUID("44444444-4444-4444-8444-444444444450"),
    UUID("44444444-4444-4444-8444-444444444451"),
    UUID("44444444-4444-4444-8444-444444444452"),
)


def _cgroup_path(unit_id: UUID) -> Path:
    return Path(
        f"/sys/fs/cgroup/user.slice/user-{os.geteuid()}.slice/"
        f"user@{os.geteuid()}.service/markweavet70{unit_id.hex}.slice"
    )


def _systemd_slice_name(unit_id: UUID) -> str:
    return f"markweavet70{unit_id.hex}.slice"


def _container_name(unit_id: UUID) -> str:
    return f"markweave-reverse-{unit_id.hex}"


def _await_cgroup_events(unit_id: UUID, expected: str) -> str:
    events = _cgroup_path(unit_id) / "cgroup.events"
    return await_cgroup_events(lambda: events.read_text(encoding="ascii"), expected)


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


def _remove_managed_units() -> None:
    # Remove every managed container first: reconciliation discovers all of them,
    # so one leftover would make every later broker start fail closed.
    _podman("rm", "--force", "--ignore", *map(_container_name, UNIT_IDS))
    for unit_id in UNIT_IDS:
        _cleanup_systemd_slice(unit_id)


@pytest.fixture(autouse=True)
def isolated_managed_units() -> Iterator[None]:
    """Leave no managed unit behind, even when the test body fails."""

    yield
    _remove_managed_units()


@pytest.fixture(scope="module")
def controlled_image() -> Iterator[tuple[str, str]]:
    _remove_managed_units()
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
        _remove_managed_units()
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


@pytest.mark.integration
def test_workspace_overlay_rejects_optimized_wrong_interpreter_base(
    controlled_image: tuple[str, str],
) -> None:
    del controlled_image
    base = os.environ.get("MARKWEAVE_T70_PODMAN_TEST_IMAGE", DEFAULT_BASE_IMAGE)
    fixture_root = ROOT / "tests/integration/broker/fixtures"
    try:
        _podman(
            "build",
            "--format",
            "oci",
            "--tag",
            INCOMPATIBLE_BASE_IMAGE,
            "--file",
            str(fixture_root / "IncompatibleWorkspaceBase.Containerfile"),
            "--build-arg",
            f"BASE_IMAGE={base}",
            str(fixture_root),
        )
        rejected = _podman(
            "build",
            "--format",
            "oci",
            "--tag",
            REJECTED_WORKSPACE_IMAGE,
            "--file",
            str(fixture_root / "WorkspaceContainerfile"),
            "--build-arg",
            f"BASE_IMAGE={INCOMPATIBLE_BASE_IMAGE}",
            str(ROOT),
            check=False,
        )
        assert rejected.returncode != 0
        assert (
            _podman("image", "exists", REJECTED_WORKSPACE_IMAGE, check=False).returncode
            == 1
        )
    finally:
        _podman("image", "rm", "--force", REJECTED_WORKSPACE_IMAGE, check=False)
        _podman("image", "rm", "--force", INCOMPATIBLE_BASE_IMAGE, check=False)


@pytest.mark.integration
def test_workspace_overlay_rejects_compatible_base_with_missing_dependency(
    controlled_image: tuple[str, str],
) -> None:
    del controlled_image
    base = os.environ.get("MARKWEAVE_T70_PODMAN_TEST_IMAGE", DEFAULT_BASE_IMAGE)
    fixture_root = ROOT / "tests/integration/broker/fixtures"
    try:
        _podman(
            "build",
            "--format",
            "oci",
            "--tag",
            MISSING_DEPENDENCY_BASE_IMAGE,
            "--file",
            str(fixture_root / "MissingWorkspaceDependencyBase.Containerfile"),
            "--build-arg",
            f"BASE_IMAGE={base}",
            str(fixture_root),
        )
        rejected = _podman(
            "build",
            "--format",
            "oci",
            "--tag",
            REJECTED_DEPENDENCY_WORKSPACE_IMAGE,
            "--file",
            str(fixture_root / "WorkspaceContainerfile"),
            "--build-arg",
            f"BASE_IMAGE={MISSING_DEPENDENCY_BASE_IMAGE}",
            str(ROOT),
            check=False,
        )
        assert rejected.returncode != 0
        assert (
            _podman(
                "image", "exists", REJECTED_DEPENDENCY_WORKSPACE_IMAGE, check=False
            ).returncode
            == 1
        )
    finally:
        _podman(
            "image", "rm", "--force", REJECTED_DEPENDENCY_WORKSPACE_IMAGE, check=False
        )
        _podman("image", "rm", "--force", MISSING_DEPENDENCY_BASE_IMAGE, check=False)


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


def _runtime(  # noqa: PLR0913 - explicit runtime seams
    repository: str,
    hooks_directory: Path,
    command: Callable[..., tuple[int, bytes]] | None = None,
    *,
    emptiness_wait: CgroupEmptinessWait | None = None,
    cgroup_read: Callable[[Path], bytes] | None = None,
    podman: Path = PODMAN,
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
            podman, PodmanCommandLimits(20), environment=environment
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
        emptiness_wait=emptiness_wait,
        cgroup_read=cgroup_read,
    )


def _service(  # noqa: PLR0913 - optional emptiness observation seams
    tmp_path: Path,
    repository: str,
    policy: BrokerPolicy,
    unit_id: UUID,
    *,
    emptiness_wait: CgroupEmptinessWait | None = None,
    cgroup_read: Callable[[Path], bytes] | None = None,
) -> tuple[IsolationBrokerService, SQLiteBrokerInventory, PodmanIsolationRuntime]:
    inventory = SQLiteBrokerInventory(
        tmp_path / "inventory.sqlite3", b"i" * 32, max_records=8
    )
    hooks = tmp_path / "hooks"
    hooks.mkdir(mode=0o700, exist_ok=True)
    runtime = _runtime(
        repository, hooks, emptiness_wait=emptiness_wait, cgroup_read=cgroup_read
    )
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
    ) == POPULATED_CGROUP_EVENTS

    runtime.hard_terminate(runtime_unit)
    # Podman reports the unit exited before conmon and its cleanup command leave
    # the slice, so the kernel clears the populated flag asynchronously.
    assert (
        _await_cgroup_events(created.unit_id, EMPTY_CGROUP_EVENTS)
        == EMPTY_CGROUP_EVENTS
    )

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


def _systemd_run(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("CONTAINER_HOST", None)
    return subprocess.run(
        (str(SYSTEMD_RUN), *arguments),
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
    )


MEMBER_SCRIPT = """\
while [ ! -e "$1" ]; do
  sleep 0.05
done
"""


def _start_slice_member(unit_id: UUID, directory: Path, release: Path) -> str:
    """Run a foreign process in the unit slice until ``release`` exists."""

    script = directory / "slice-member.sh"
    script.write_text(MEMBER_SCRIPT, encoding="ascii")
    member = f"markweave-t137-member-{unit_id.hex}.service"
    _systemd_run(
        "--user",
        f"--slice={_systemd_slice_name(unit_id)}",
        f"--unit={member}",
        "--collect",
        "--quiet",
        "/bin/sh",
        str(script),
        str(release),
    )
    assert _systemctl("--user", "is-active", member).stdout.strip() == "active"
    return member


def _await_member_stopped(member: str) -> None:
    deadline = time.monotonic() + 10
    state = "active"
    while time.monotonic() < deadline:
        state = _systemctl("--user", "is-active", member, check=False).stdout.strip()
        if state != "active":
            break
        time.sleep(0.05)
    assert state != "active"


def _scope_populated(scope: Path) -> bool:
    try:
        events = (scope / "cgroup.events").read_text(encoding="ascii")
    except FileNotFoundError:
        return False
    return events.startswith("populated 1\n")


@pytest.mark.integration
def test_terminate_waits_for_a_unit_member_that_exits_within_the_bound(
    tmp_path: Path, controlled_image: tuple[str, str], mocker: MockerFixture
) -> None:
    repository, digest = controlled_image
    unit_id = UNIT_IDS[6]
    release = tmp_path / "release-member"
    container_scope: list[Path] = []
    observations: list[bytes] = []
    release_observation: list[int] = []

    def observing_read(path: Path) -> bytes:
        value = podman_runtime._read_cgroup_events(path)
        observations.append(value)
        # Release the member only once the container's own processes are gone,
        # so the broker must observe the still-populated slice and retry.
        if (
            container_scope
            and not release.exists()
            and not _scope_populated(container_scope[0])
        ):
            release_observation.append(len(observations) - 1)
            release.touch()
        return value

    service, inventory, runtime = _service(
        tmp_path, repository, _policy(digest), unit_id, cgroup_read=observing_read
    )
    attempt_id = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
    created = service.create(ReplayPosition(PRINCIPAL, 1), attempt_id)
    runtime_unit = runtime.discover(limit=1)[0]
    inspected = json.loads(
        _podman(
            "container", "inspect", runtime_unit.container_id, "--format", "json"
        ).stdout
    )[0]
    container_scope.append(
        Path("/sys/fs/cgroup") / inspected["State"]["CgroupPath"].lstrip("/")
    )
    assert _scope_populated(container_scope[0])
    member = _start_slice_member(unit_id, tmp_path, release)
    transitions = mocker.spy(inventory, "transition")
    removals = mocker.spy(inventory, "mark_removed")
    try:
        observations.clear()
        proof = service.terminate(PRINCIPAL, attempt_id, created.unit_id)
    finally:
        _systemctl("--user", "stop", member, check=False)

    assert len(release_observation) == 1
    released_at = release_observation[0]
    assert observations[released_at] == POPULATED_CGROUP_EVENTS.encode()
    assert len(observations) > released_at + 1
    assert observations[-1] == EMPTY_CGROUP_EVENTS.encode()
    assert service.ready is True
    assert [call.kwargs["target"] for call in transitions.call_args_list] == [
        ManagedUnitState.EXIT_CONFIRMED,
        ManagedUnitState.EMPTY_CONFIRMED,
    ]
    assert removals.call_count == 1
    retained = inventory.get(created.unit_id)
    assert retained is not None and retained.state is ManagedUnitState.REMOVED
    assert inventory.get_proof(created.unit_id) == proof
    assert proof.empty_evidence == retained.empty_evidence
    assert service.proof(PRINCIPAL, attempt_id, created.unit_id) == proof
    assert runtime.discover(limit=1) == ()
    _assert_systemd_slice_clean(unit_id)


@pytest.mark.integration
def test_terminate_refuses_proof_until_reconciliation_after_a_member_outlives_the_bound(
    tmp_path: Path, controlled_image: tuple[str, str], mocker: MockerFixture
) -> None:
    repository, digest = controlled_image
    unit_id = UNIT_IDS[7]
    release = tmp_path / "release-member"
    service, inventory, runtime = _service(
        tmp_path,
        repository,
        _policy(digest),
        unit_id,
        emptiness_wait=CgroupEmptinessWait(1.0, 0.05),
    )
    attempt_id = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
    created = service.create(ReplayPosition(PRINCIPAL, 1), attempt_id)
    member = _start_slice_member(unit_id, tmp_path, release)
    transitions = mocker.spy(inventory, "transition")
    try:
        started = time.monotonic()
        with pytest.raises(BrokerError) as refused:
            service.terminate(PRINCIPAL, attempt_id, created.unit_id)
        assert refused.value.category is BrokerErrorCategory.TERMINATION_UNPROVEN
        assert time.monotonic() - started >= 1.0
        assert service.ready is False
        with pytest.raises(BrokerError) as unavailable:
            service.status(PRINCIPAL, attempt_id, created.unit_id)
        assert unavailable.value.category is (
            BrokerErrorCategory.RECONCILIATION_INCOMPLETE
        )
        unproven = inventory.get(created.unit_id)
        assert unproven is not None
        assert unproven.state is ManagedUnitState.EXIT_CONFIRMED
        assert unproven.exit_evidence is not None
        assert unproven.empty_evidence is None
        assert inventory.get_proof(created.unit_id) is None

        # While the member still holds the slice, a reconciliation sweep must
        # neither prove emptiness nor restore readiness.
        with pytest.raises(BrokerError) as sweep:
            service.start()
        assert sweep.value.category is BrokerErrorCategory.RECONCILIATION_INCOMPLETE
        assert service.ready is False
        swept = inventory.get(created.unit_id)
        assert swept is not None and swept.state is ManagedUnitState.EXIT_CONFIRMED
        assert inventory.get_proof(created.unit_id) is None

        release.touch()
        _await_member_stopped(member)
    finally:
        _systemctl("--user", "stop", member, check=False)

    service.start()

    assert service.ready is True
    assert [call.kwargs["target"] for call in transitions.call_args_list] == [
        ManagedUnitState.EXIT_CONFIRMED,
        ManagedUnitState.EMPTY_CONFIRMED,
    ]
    reconciled = inventory.get(created.unit_id)
    assert reconciled is not None and reconciled.state is ManagedUnitState.REMOVED
    assert reconciled.exit_evidence == unproven.exit_evidence
    proof = service.proof(PRINCIPAL, attempt_id, created.unit_id)
    assert proof is not None and proof == inventory.get_proof(created.unit_id)
    assert proof.empty_evidence == reconciled.empty_evidence
    assert service.status(PRINCIPAL, attempt_id, created.unit_id).state is (
        ManagedUnitState.REMOVED
    )
    assert runtime.discover(limit=1) == ()
    _assert_systemd_slice_clean(unit_id)


TRANSPORT_OPERATION_SECONDS = 4.0
SERVER_PRINCIPAL = AuthenticatedPrincipal(UUID("55555555-5555-4555-8555-555555555555"))


@pytest.fixture(scope="module")
def transport_certificates(
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("t137-mtls")
    ca = _ca(root, "ca")
    server_certificate, server_key = _issue(
        root, name="server", ca_name="ca", uri=SERVER_URI, eku="serverAuth"
    )
    client_certificate, client_key = _issue(
        root, name="client", ca_name="ca", uri=CLIENT_URI, eku="clientAuth"
    )
    return {
        "ca": ca,
        "server_certificate": server_certificate,
        "server_key": server_key,
        "client_certificate": client_certificate,
        "client_key": client_key,
    }


def _transport_pair(
    transport: str,
    tmp_path: Path,
    dispatcher: BrokerDispatcher,
    certificates: dict[str, Path],
) -> tuple[
    UnixBrokerServer | MtlsBrokerServer,
    Callable[[], UnixBrokerClient | MtlsBrokerClient],
]:
    """Return a real server and a client factory to call once it is bound."""
    if transport == "unix":
        sockets = tmp_path / "socket"
        sockets.mkdir(mode=0o700)
        path = sockets / "broker.sock"
        unix_server = UnixBrokerServer(
            path,
            expected_client_uid=os.geteuid(),
            principal=PRINCIPAL,
            dispatcher=dispatcher,
            limits=UnixTransportLimits(TRANSPORT_OPERATION_SECONDS, 2, 2, 2),
        )
        return unix_server, lambda: UnixBrokerClient(
            path,
            expected_server_uid=os.geteuid(),
            expected_principal=PRINCIPAL,
            operation_timeout_seconds=TRANSPORT_OPERATION_SECONDS + 2,
        )
    mtls_server = MtlsBrokerServer(
        MtlsEndpoint("127.0.0.1", 0),
        local_identity=MtlsLocalIdentity(
            certificates["ca"],
            certificates["server_certificate"],
            certificates["server_key"],
            SERVER_URI,
            SERVER_PRINCIPAL,
        ),
        client_identity=MtlsPeerIdentity(
            CLIENT_URI, (_pin(certificates["client_certificate"]),), PRINCIPAL
        ),
        dispatcher=dispatcher,
        limits=MtlsTransportLimits(TRANSPORT_OPERATION_SECONDS, 2, 4, 4, 4, 4),
    )
    return mtls_server, lambda: MtlsBrokerClient(
        mtls_server.endpoint,
        local_identity=MtlsLocalIdentity(
            certificates["ca"],
            certificates["client_certificate"],
            certificates["client_key"],
            CLIENT_URI,
            PRINCIPAL,
        ),
        server_identity=MtlsPeerIdentity(
            SERVER_URI, (_pin(certificates["server_certificate"]),), SERVER_PRINCIPAL
        ),
        operation_timeout_seconds=TRANSPORT_OPERATION_SECONDS + 2,
    )


SLOW_PODMAN = """\
#!/bin/sh
# Delay one broker-issued Podman subcommand while its delay file exists.
for argument in "$@"; do
  if [ -f "{delays}/$argument" ]; then
    sleep "$(cat "{delays}/$argument")"
    break
  fi
done
exec /usr/bin/podman "$@"
"""
# Each delayed step consumes most of the 3.6-second dispatch budget of a
# 4-second transport deadline, so a later step must be refused before it.
DEADLINE_SCENARIOS = {
    "persistent-member": {},
    "slow-kill": {"kill": "3.4"},
    "slow-inspect": {"inspect": "1.3"},
    "slow-removal": {"rm": "10"},
    # The kill exhausts its execution budget and its killed group is reaped late.
    "unreaped-kill": {"kill": "10"},
}


def _slow_podman(directory: Path) -> tuple[Path, Path]:
    delays = directory / "delays"
    delays.mkdir(mode=0o700)
    wrapper = directory / "slow-podman"
    wrapper.write_text(SLOW_PODMAN.format(delays=delays), encoding="ascii")
    wrapper.chmod(0o700)
    return wrapper, delays


@pytest.mark.integration
@pytest.mark.parametrize("transport", ["unix", "mtls"])
@pytest.mark.parametrize("scenario", list(DEADLINE_SCENARIOS))
def test_termination_refusal_returns_within_the_transport_deadline(  # noqa: PLR0913, PLR0915, PLR0917 - one linear refusal and recovery scenario
    tmp_path: Path,
    controlled_image: tuple[str, str],
    transport_certificates: dict[str, Path],
    transport: str,
    scenario: str,
    mocker: MockerFixture,
) -> None:
    repository, digest = controlled_image
    unit_id = UNIT_IDS[
        8
        + 2 * list(DEADLINE_SCENARIOS).index(scenario)
        + ["unix", "mtls"].index(transport)
    ]
    wait = podman_emptiness_wait(TRANSPORT_OPERATION_SECONDS)
    assert wait == CgroupEmptinessWait(1.0, 0.05)
    inventory = SQLiteBrokerInventory(
        tmp_path / "inventory.sqlite3", b"i" * 32, max_records=8
    )
    hooks = tmp_path / "hooks"
    hooks.mkdir(mode=0o700)
    wrapper, delays = _slow_podman(tmp_path)
    service = IsolationBrokerService(
        inventory,
        _runtime(repository, hooks, emptiness_wait=wait, podman=wrapper),
        _policy(digest),
        max_discovered_units=8,
        unit_id_factory=lambda: unit_id,
    )
    server, client_factory = _transport_pair(
        transport, tmp_path, BrokerDispatcher(service), transport_certificates
    )
    release = tmp_path / "release-member"
    attempt_id = uuid4()
    member: str | None = None
    reapers: list[Callable[[], None]] = []
    server.start()
    try:
        client = client_factory()
        created = client.request(CreateRequest(uuid4(), 1, attempt_id))
        assert isinstance(created, CreateResponse)
        assert created.unit_id == unit_id
        if scenario == "persistent-member":
            member = _start_slice_member(unit_id, tmp_path, release)
        for subcommand, seconds in DEADLINE_SCENARIOS[scenario].items():
            (delays / subcommand).write_text(seconds, encoding="ascii")
        if scenario == "unreaped-kill":
            reapers.append(install_slow_reaping(mocker, 0.6))
        terminate = TerminateRequest(uuid4(), 1, attempt_id, unit_id)
        started = time.monotonic()
        refused = client.request(terminate)
        elapsed = time.monotonic() - started

        assert refused == ErrorResponse(
            terminate.request_id,
            BrokerOperation.TERMINATE,
            BrokerErrorCategory.TERMINATION_UNPROVEN,
        )
        assert elapsed < TRANSPORT_OPERATION_SECONDS
        if scenario == "persistent-member":
            assert elapsed >= wait.timeout_seconds
        # The dispatch watchdog never fired: the broker still serves.
        assert server.failed is False
        assert server.stopping is False
        ready = ReadyRequest(uuid4(), 1)
        assert client.request(ready) == ReadyResponse(ready.request_id, False)
        unproven = inventory.get(unit_id)
        assert unproven is not None
        assert unproven.state is not ManagedUnitState.REMOVED
        assert inventory.get_proof(unit_id) is None

        for delay in delays.iterdir():
            delay.unlink()
        for reap in reapers:
            reap()
        if member is not None:
            release.touch()
            _await_member_stopped(member)
        reconciled = client.reconcile(ReconciliationRequest(uuid4(), 0))
        assert isinstance(reconciled, ReconciliationResponse)
        ready = ReadyRequest(uuid4(), 1)
        assert client.request(ready) == ReadyResponse(ready.request_id, True)
        removed = inventory.get(unit_id)
        assert removed is not None and removed.state is ManagedUnitState.REMOVED
        assert inventory.get_proof(unit_id) is not None
        assert server.failed is False
    finally:
        if member is not None:
            _systemctl("--user", "stop", member, check=False)
        server.stop()
    _assert_systemd_slice_clean(unit_id)
