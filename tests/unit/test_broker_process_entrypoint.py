"""Tests for the dependency-light broker console boundary."""

from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from markweave.broker_process import main


@pytest.mark.unit
def test_entrypoint_rejects_arguments_before_importing_broker(
    capfd: pytest.CaptureFixture[str], mocker: MockerFixture
) -> None:
    imported = mocker.patch("builtins.__import__", side_effect=AssertionError)

    assert main([]) == 2

    assert capfd.readouterr() == ("", "broker configuration failed\n")
    imported.assert_not_called()


@pytest.mark.unit
def test_entrypoint_delegates_exact_config_argument(
    tmp_path, mocker: MockerFixture
) -> None:
    run = mocker.patch("markweave.broker.process.main", return_value=0)
    config = tmp_path / "broker.json"

    assert main([str(config)]) == 0

    run.assert_called_once_with((str(config),))


@pytest.mark.unit
def test_entrypoint_contains_dependency_or_import_failure(
    capfd: pytest.CaptureFixture[str], mocker: MockerFixture
) -> None:
    mocker.patch("markweave.broker.process.main", side_effect=RuntimeError("private"))

    assert main(["/private/config.json"]) == 1

    assert capfd.readouterr() == ("", "broker runtime failed\n")
