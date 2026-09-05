"""Unit checks for the controlled real-Podman lifecycle fixture."""

from __future__ import annotations

import signal

import pytest
from pytest_mock import MockerFixture

from tests.integration.broker.fixtures import attempt_main


@pytest.mark.unit
def test_attempt_parent_ignores_signals_before_fork_and_then_publishes_ready(
    mocker: MockerFixture,
) -> None:
    calls: list[tuple[object, ...]] = []
    mocker.patch.object(
        attempt_main.signal,
        "signal",
        side_effect=lambda signum, handler: calls.append(("signal", signum, handler)),
    )
    mocker.patch.object(
        attempt_main.os, "fork", side_effect=lambda: calls.append(("fork",)) or 123
    )
    mocker.patch.object(
        attempt_main.Path,
        "write_text",
        autospec=True,
        side_effect=lambda path, value, **kwargs: calls.append(
            ("ready", path, value, kwargs)
        ),
    )
    mocker.patch.object(
        attempt_main, "_wait", side_effect=lambda: calls.append(("wait",))
    )

    attempt_main.main()

    assert calls == [
        ("signal", signal.SIGINT, signal.SIG_IGN),
        ("signal", signal.SIGTERM, signal.SIG_IGN),
        ("fork",),
        ("ready", attempt_main.Path("/work/ready"), "ready", {"encoding": "ascii"}),
        ("wait",),
    ]


@pytest.mark.unit
def test_attempt_child_never_publishes_readiness(mocker: MockerFixture) -> None:
    write = mocker.patch.object(attempt_main.Path, "write_text", autospec=True)
    mocker.patch.object(attempt_main.signal, "signal")
    mocker.patch.object(attempt_main.os, "fork", return_value=0)
    wait = mocker.patch.object(attempt_main, "_wait")

    attempt_main.main()

    wait.assert_called_once_with()
    write.assert_not_called()
