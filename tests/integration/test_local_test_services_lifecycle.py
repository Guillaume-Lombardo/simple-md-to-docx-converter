"""Real disposable-service cleanup after a failing test body."""

import subprocess

import pytest
from pytest_mock import MockerFixture

from scripts.testing import services

pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_postgres,
    pytest.mark.requires_s3,
]


def test_failed_test_body_removes_only_its_owned_service_resources(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
) -> None:
    # The session fixture's configured stack stays alive; this nested stack must
    # clean only its own project and leave the outer test environment intact.
    for variable in services.VARIABLES:
        monkeypatch.delenv(variable, raising=False)
    compose = mocker.spy(services, "_compose")
    with (
        pytest.raises(RuntimeError, match="deliberate test failure"),
        services.distributed_services(),
    ):
        raise RuntimeError("deliberate test failure")
    project = compose.call_args_list[0].args[0]
    for resource in ("container", "volume", "network"):
        result = subprocess.run(
            [
                "docker",
                "--host",
                "unix:///var/run/docker.sock",
                resource,
                "ls",
                "--quiet",
                *(["--all"] if resource == "container" else []),
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.stdout.strip() == ""
