"""Lifecycle and isolation regressions for automatic distributed test services."""

import os
import subprocess

import pytest
from pytest_mock import MockerFixture

from scripts.testing import services

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clear_service_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in services.VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_partial_configuration_never_launches_replacements(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    monkeypatch.setenv(services.VARIABLES[0], "configured-external-service")
    compose = mocker.patch.object(services, "_compose")
    with (
        pytest.raises(RuntimeError, match="Incomplete distributed test configuration"),
        services.distributed_services(),
    ):
        pytest.fail("Partial configuration must fail before tests run")
    compose.assert_not_called()


def test_external_services_are_neither_started_nor_stopped(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    expected = dict.fromkeys(services.VARIABLES, "external")
    for name, value in expected.items():
        monkeypatch.setenv(name, value)
    compose = mocker.patch.object(services, "_compose")
    with services.distributed_services():
        assert {name: os.environ[name] for name in expected} == expected
    compose.assert_not_called()


@pytest.mark.parametrize("failure", ["none", "startup", "port", "bucket", "test"])
def test_owned_services_cleanup_on_every_lifecycle_failure(
    failure: str,
    mocker: MockerFixture,
) -> None:
    calls: list[tuple[str, ...]] = []
    projects: list[str] = []

    def compose(project: str, environment: dict[str, str], *args: str) -> str:
        projects.append(project)
        calls.append(args)
        assert environment["MARKWEAVE_LOCAL_TEST_PASSWORD"]
        if args[0] == "up" and failure == "startup":
            raise RuntimeError("startup")
        if args[0] == "port":
            return "0.0.0.0:15432" if failure == "port" else "127.0.0.1:15432"
        return ""

    mocker.patch.object(services, "_compose", side_effect=compose)
    prepare = mocker.patch.object(services, "prepare_bucket")
    if failure == "bucket":
        prepare.side_effect = RuntimeError("bucket")

    def exercise() -> None:
        with services.distributed_services():
            assert all(os.environ[name] for name in services.VARIABLES)
            assert (
                os.environ["MARKWEAVE_TEST_S3_ENDPOINT_URL"] == "http://127.0.0.1:15432"
            )
            assert os.environ["MARKWEAVE_TEST_S3_BUCKET"].startswith("test-")
            if failure == "test":
                raise RuntimeError("test failure")

    if failure == "none":
        exercise()
    else:
        with pytest.raises(RuntimeError):
            exercise()
    assert calls[-1] == ("down", "--volumes", "--remove-orphans", "--timeout", "10")
    assert len(set(projects)) == 1
    assert projects[0].startswith("markweave-tests-")
    assert not any(name in os.environ for name in services.VARIABLES)


@pytest.mark.parametrize(
    "value",
    [
        "0.0.0.0:5432",
        "::1:5432",
        "127.0.0.1:0",
        "127.0.0.1:65536",
        "127.0.0.1:123\n127.0.0.1:456",
    ],
)
def test_only_single_valid_loopback_bindings_are_accepted(value: str) -> None:
    with pytest.raises(RuntimeError):
        services._port(value)


def test_compose_failure_does_not_echo_environment_or_diagnostics(
    mocker: MockerFixture,
) -> None:
    run = mocker.patch.object(
        services.subprocess,
        "run",
        return_value=subprocess.CompletedProcess([], 1, "", "private-diagnostic"),
    )
    with pytest.raises(RuntimeError, match="Compose up failed") as error:
        services._compose("markweave-tests-example", {}, "up")
    assert "private-diagnostic" not in str(error.value)
    assert run.call_args.kwargs["timeout"] == 300
    assert "shell" not in run.call_args.kwargs


def test_missing_engine_reports_configuration_alternative(
    mocker: MockerFixture,
) -> None:
    mocker.patch.object(services.subprocess, "run", side_effect=FileNotFoundError)
    with pytest.raises(RuntimeError, match="Docker Compose"):
        services._compose("markweave-tests-example", {}, "up")
