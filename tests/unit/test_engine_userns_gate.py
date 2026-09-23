"""CI-only user namespace gate keeps engine isolation mandatory."""

from __future__ import annotations

import errno
import signal
from pathlib import Path

import pytest
import yaml
from pytest_mock import MockerFixture

from scripts.ci import engine_userns_gate as gate

pytestmark = pytest.mark.unit


def test_preflight_confirms_same_identity_and_two_new_namespaces(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(gate, "_install_no_network_filter")
    mocker.patch.object(gate.os, "unshare")
    mapping = mocker.patch.object(Path, "write_text")
    mocker.patch.object(gate.os, "geteuid", return_value=12345)
    mocker.patch.object(gate.os, "getegid", return_value=0)
    mocker.patch.object(
        gate.os, "readlink", side_effect=["user:old", "net:old", "user:new", "net:new"]
    )
    assert gate._preflight() == 0
    assert mapping.call_count == 3


@pytest.mark.parametrize(
    ("stage", "expected"),
    (
        ("seccomp", 43),
        ("unshare", 42),
        ("mapping", 43),
    ),
)
def test_preflight_only_identifies_unshare_eperm_as_runner_allowance_case(
    mocker: MockerFixture, stage: str, expected: int
) -> None:
    seccomp = mocker.patch.object(gate, "_install_no_network_filter")
    unshare = mocker.patch.object(gate.os, "unshare")
    mapping = mocker.patch.object(Path, "write_text")
    mocker.patch.object(gate.os, "readlink", side_effect=["user:old", "net:old"])
    {"seccomp": seccomp, "unshare": unshare, "mapping": mapping}[
        stage
    ].side_effect = OSError(errno.EPERM, "denied")
    assert gate._preflight() == expected


def test_passing_preflight_runs_command_without_sysctl_change(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(gate, "_run_preflight", return_value=0)
    set_policy = mocker.patch.object(gate, "_set_apparmor_userns_restriction")
    run = mocker.patch.object(gate, "_run_command", return_value=7)
    assert gate.main(["--", "uv", "run", "pytest"]) == 7
    run.assert_called_once_with(["uv", "run", "pytest"])
    set_policy.assert_not_called()


def test_hosted_runner_temporarily_allows_userns_and_restores_it(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(gate, "_run_preflight", side_effect=[42, 0])
    mocker.patch.object(gate, "_is_ephemeral_ubuntu_runner", return_value=True)
    mocker.patch.object(Path, "read_text", return_value="1\n")
    set_policy = mocker.patch.object(
        gate, "_set_apparmor_userns_restriction", return_value=True
    )
    run = mocker.patch.object(gate, "_run_command", return_value=7)
    assert gate.main(["--", "uv", "run", "pytest"]) == 7
    run.assert_called_once_with(["uv", "run", "pytest"])
    assert [call.args[0] for call in set_policy.call_args_list] == ["0", "1"]


@pytest.mark.parametrize("second_probe", (42, 43))
def test_runner_never_runs_suite_when_second_preflight_fails(
    mocker: MockerFixture, second_probe: int
) -> None:
    mocker.patch.object(gate, "_run_preflight", side_effect=[42, second_probe])
    mocker.patch.object(gate, "_is_ephemeral_ubuntu_runner", return_value=True)
    mocker.patch.object(Path, "read_text", return_value="1\n")
    set_policy = mocker.patch.object(
        gate, "_set_apparmor_userns_restriction", return_value=True
    )
    run = mocker.patch.object(gate, "_run_command")
    assert gate.main(["--", "uv", "run", "pytest"]) == 1
    run.assert_not_called()
    assert [call.args[0] for call in set_policy.call_args_list] == ["0", "1"]


def test_runner_fails_if_policy_cannot_be_restored(mocker: MockerFixture) -> None:
    mocker.patch.object(gate, "_run_preflight", side_effect=[42, 0])
    mocker.patch.object(gate, "_is_ephemeral_ubuntu_runner", return_value=True)
    mocker.patch.object(Path, "read_text", return_value="1\n")
    mocker.patch.object(
        gate, "_set_apparmor_userns_restriction", side_effect=[True, False]
    )
    mocker.patch.object(gate, "_run_command", return_value=0)
    assert gate.main(["--", "uv", "run", "pytest"]) == 1


def test_catchable_runner_interrupt_restores_policy(mocker: MockerFixture) -> None:
    mocker.patch.object(gate, "_run_preflight", side_effect=[42, 0])
    mocker.patch.object(gate, "_is_ephemeral_ubuntu_runner", return_value=True)
    mocker.patch.object(Path, "read_text", return_value="1\n")
    handlers: dict[int, object] = {}

    def set_policy_value(value: str) -> bool:
        if value == "1":
            assert handlers[signal.SIGTERM] == signal.SIG_IGN
        return True

    set_policy = mocker.patch.object(
        gate, "_set_apparmor_userns_restriction", side_effect=set_policy_value
    )

    def record_handler(signum: int, handler: object) -> object:
        handlers[signum] = handler
        return signal.SIG_DFL

    mocker.patch.object(gate.signal, "signal", side_effect=record_handler)

    def interrupt(_command: list[str]) -> int:
        handler = handlers[signal.SIGTERM]
        assert callable(handler)
        handler(signal.SIGTERM, None)
        return 0

    mocker.patch.object(gate, "_run_command", side_effect=interrupt)
    with pytest.raises(SystemExit, match=str(128 + signal.SIGTERM)):
        gate.main(["--", "uv", "run", "pytest"])
    assert [call.args[0] for call in set_policy.call_args_list] == ["0", "1"]


def test_non_hosted_runner_cannot_change_sysctl(mocker: MockerFixture) -> None:
    mocker.patch.object(gate, "_run_preflight", return_value=42)
    mocker.patch.object(gate, "_is_ephemeral_ubuntu_runner", return_value=False)
    set_policy = mocker.patch.object(gate, "_set_apparmor_userns_restriction")
    assert gate.main(["--", "uv", "run", "pytest"]) == 1
    set_policy.assert_not_called()


@pytest.mark.parametrize(
    ("environment", "release", "expected"),
    (
        (
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
            },
            {"ID": "ubuntu", "VERSION_ID": "24.04"},
            True,
        ),
        (
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "self-hosted",
                "RUNNER_OS": "Linux",
            },
            {"ID": "ubuntu", "VERSION_ID": "24.04"},
            False,
        ),
        (
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
            },
            {"ID": "ubuntu", "VERSION_ID": "22.04"},
            False,
        ),
    ),
)
def test_temporary_allowance_is_only_for_hosted_ubuntu_2404(
    mocker: MockerFixture,
    environment: dict[str, str],
    release: dict[str, str],
    expected: bool,
) -> None:
    mocker.patch.dict(gate.os.environ, environment, clear=True)
    mocker.patch.object(gate.platform, "freedesktop_os_release", return_value=release)
    assert gate._is_ephemeral_ubuntu_runner() is expected


def test_workflow_gates_native_python_shards_and_document_engines() -> None:
    workflow = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml").read_text()
    )
    light_shard = next(
        step
        for step in workflow["jobs"]["python-tests"]["steps"]
        if step.get("name") == "Run a complementary light test shard"
    )
    heavy = next(
        step
        for step in workflow["jobs"]["heavy"]["steps"]
        if step.get("name") == "Run selected domain suite without a shell"
    )
    assert "scripts.ci.engine_userns_gate --" in light_shard["run"]
    assert 'if [[ "$CI_DOMAIN" == "document-engines" ]]' in heavy["run"]
    assert "scripts.ci.engine_userns_gate --" in heavy["run"]
    assert 'scripts.ci.run_domain "$CI_DOMAIN"' in heavy["run"]
