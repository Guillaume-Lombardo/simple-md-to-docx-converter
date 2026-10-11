"""Unit tests for isolated document-engine version probes (T106, D1)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from markweave.conversion.engine_launcher import isolated_engine_command
from markweave.conversion.engine_versions import (
    ENGINE_NAMES,
    EngineObservation,
    EngineProbeConfig,
    EngineProbeError,
    EngineVersionProbe,
    EngineVersionStatus,
    approved_engine_versions,
    engine_display_name,
    parse_engine_version,
)
from markweave.conversion.runtime_manifest import COMPONENT_VERSIONS

pytestmark = pytest.mark.unit


def probe(tmp_path: Path, **overrides: float) -> EngineVersionProbe:
    values = {
        "timeout_seconds": 5.0,
        "termination_grace_seconds": 0.5,
        "poll_seconds": 0.1,
        **overrides,
    }
    return EngineVersionProbe(
        EngineProbeConfig(workspace_root=tmp_path, **values),
        {"PATH": "/approved/bin", "SECRET_VALUE": "must-not-pass"},
    )


def fake_process(mocker, output: bytes, return_code: int = 0):
    process = mocker.Mock(pid=4321)
    process.wait.return_value = return_code

    def start(arguments, **options):
        options["stdout"].write(output)
        return process

    return process, start


def test_approved_pins_are_the_shared_component_versions() -> None:
    shared = dict(COMPONENT_VERSIONS)
    assert approved_engine_versions() == {name: shared[name] for name in ENGINE_NAMES}
    assert set(ENGINE_NAMES) == {"pandoc", "libreoffice", "mermaid-cli", "chromium"}
    assert [engine_display_name(name) for name in ENGINE_NAMES] == [
        "Pandoc",
        "LibreOffice",
        "Mermaid CLI",
        "Chromium",
    ]


@pytest.mark.parametrize(
    ("name", "output", "expected"),
    [
        ("pandoc", "pandoc 3.10.2\nFeatures: +server\n", "3.10.2"),
        ("pandoc", "\n  pandoc.exe 3.9.0.2  \n", "3.9.0.2"),
        (
            "libreoffice",
            "LibreOffice 26.2.5.2 cd7284b4cbbfeb507e630c1aac019f4157393acb\n",
            "26.2.5.2",
        ),
        ("libreoffice", "LibreOffice 7.6.4.1\n", "7.6.4.1"),
        ("mermaid-cli", "11.17.0\n", "11.17.0"),
        ("mermaid-cli", "v11.16.0-beta.1\n", "11.16.0-beta.1"),
        ("chromium", "Google Chrome 151.0.7922.173 \n", "151.0.7922.173"),
        ("chromium", "Chromium 150.0.1.2 built on Debian\n", "150.0.1.2"),
        ("chromium", "Google Chrome for Testing 151.0.7922.173\n", "151.0.7922.173"),
        ("pandoc", "", None),
        ("pandoc", "pandoc-citeproc 0.17\n", None),
        ("libreoffice", "Collabora Office 24.4\n", None),
        ("mermaid-cli", "mermaid\n", None),
        ("chromium", "Firefox 140.0\n", None),
    ],
)
def test_version_output_is_parsed_strictly(
    name: str, output: str, expected: str | None
) -> None:
    assert parse_engine_version(name, output) == expected


def test_probe_runs_fixed_isolated_argv_in_private_workspace(
    tmp_path: Path, mocker
) -> None:
    process, start = fake_process(mocker, b"pandoc 3.10.2\n")
    popen = mocker.patch(
        "markweave.conversion.engine_versions.subprocess.Popen", side_effect=start
    )

    observation = probe(tmp_path).observe("pandoc", "/engines/pandoc")

    assert observation == EngineObservation(
        "pandoc", "3.10.2", "3.10.2", EngineVersionStatus.PINNED
    )
    assert observation.pinned
    assert observation.as_manifest() == {
        "name": "pandoc",
        "approved_version": "3.10.2",
        "actual_version": "3.10.2",
        "status": "pinned",
    }
    arguments, options = popen.call_args
    assert arguments[0] == isolated_engine_command(["/engines/pandoc", "--version"])
    assert options["shell"] is False
    assert options["start_new_session"] is True
    assert options["close_fds"] is True
    assert options["stdin"] is subprocess.DEVNULL
    assert options["stderr"] is subprocess.DEVNULL
    workspace = options["cwd"]
    environment = options["env"]
    assert "SECRET_VALUE" not in environment
    assert environment["PATH"] == "/approved/bin"
    assert environment["HOME"] == str(workspace / "home")
    assert environment["XDG_RUNTIME_DIR"] == str(workspace / "runtime")
    assert not workspace.exists()
    assert list(tmp_path.iterdir()) == []
    process.wait.assert_called_once()


@pytest.mark.parametrize(
    ("output", "actual", "status"),
    [
        (b"pandoc 3.9.0\n", "3.9.0", EngineVersionStatus.DIFFERENT),
        (b"unexpected\n", None, EngineVersionStatus.UNKNOWN),
        (b"\xff\xfe", None, EngineVersionStatus.UNKNOWN),
    ],
)
def test_probe_reports_different_and_unknown_versions(
    tmp_path: Path, mocker, output: bytes, actual: str | None, status
) -> None:
    _, start = fake_process(mocker, output)
    mocker.patch(
        "markweave.conversion.engine_versions.subprocess.Popen", side_effect=start
    )

    observation = probe(tmp_path).observe("pandoc", "pandoc")

    assert (observation.actual_version, observation.status) == (actual, status)
    assert not observation.pinned


def test_probe_reads_only_bounded_output(tmp_path: Path, mocker) -> None:
    _, start = fake_process(mocker, b"pandoc 3.10.2\n" + b"x" * 100_000)
    mocker.patch(
        "markweave.conversion.engine_versions.subprocess.Popen", side_effect=start
    )
    assert probe(tmp_path).observe("pandoc", "pandoc").pinned


@pytest.mark.parametrize("return_code", [1, 125, 127])
def test_failed_or_missing_engine_is_unavailable(
    tmp_path: Path, mocker, return_code: int
) -> None:
    _, start = fake_process(mocker, b"pandoc 3.10.2\n", return_code)
    mocker.patch(
        "markweave.conversion.engine_versions.subprocess.Popen", side_effect=start
    )
    with pytest.raises(EngineProbeError) as captured:
        probe(tmp_path).observe("pandoc", "pandoc")
    assert (captured.value.name, captured.value.reason) == ("pandoc", "unavailable")


def test_unstartable_launcher_is_unavailable(tmp_path: Path, mocker) -> None:
    mocker.patch(
        "markweave.conversion.engine_versions.subprocess.Popen",
        side_effect=OSError("no launcher"),
    )
    with pytest.raises(EngineProbeError, match="unavailable"):
        probe(tmp_path).observe("libreoffice", "soffice")


def test_workspace_failure_is_unavailable(tmp_path: Path, mocker) -> None:
    mocker.patch(
        "markweave.conversion.engine_versions.tempfile.TemporaryDirectory",
        side_effect=OSError("full"),
    )
    with pytest.raises(EngineProbeError, match="unavailable"):
        probe(tmp_path).observe("pandoc", "pandoc")


def test_unreadable_output_is_unavailable(tmp_path: Path, mocker) -> None:
    _, start = fake_process(mocker, b"pandoc 3.10.2\n")
    mocker.patch(
        "markweave.conversion.engine_versions.subprocess.Popen", side_effect=start
    )
    original_open = Path.open

    def open_output(path: Path, mode: str = "r", *args, **kwargs):
        if mode == "rb":
            raise OSError("unreadable")
        return original_open(path, mode, *args, **kwargs)

    mocker.patch.object(Path, "open", open_output)
    with pytest.raises(EngineProbeError, match="unavailable"):
        probe(tmp_path).observe("pandoc", "pandoc")


def test_probe_timeout_terminates_then_kills_the_process_group(
    tmp_path: Path, mocker
) -> None:
    process = mocker.Mock(pid=4321)
    process.wait.side_effect = [
        subprocess.TimeoutExpired("probe", 0.1),
        subprocess.TimeoutExpired("probe", 0.5),
        0,
    ]
    mocker.patch(
        "markweave.conversion.engine_versions.subprocess.Popen", return_value=process
    )
    clock = iter((0.0, 0.5, 10.0))
    mocker.patch(
        "markweave.conversion.engine_versions.time.monotonic",
        side_effect=lambda: next(clock),
    )
    killpg = mocker.patch("markweave.conversion.engine_versions.os.killpg")

    with pytest.raises(EngineProbeError) as captured:
        probe(tmp_path, timeout_seconds=1.0).observe("chromium", "chrome")

    assert captured.value.reason == "timeout"
    assert killpg.call_args_list == [mocker.call(4321, 15), mocker.call(4321, 9)]


def test_probe_honours_overall_deadline_and_vanished_group(
    tmp_path: Path, mocker
) -> None:
    process = mocker.Mock(pid=4321)
    mocker.patch(
        "markweave.conversion.engine_versions.subprocess.Popen", return_value=process
    )
    mocker.patch(
        "markweave.conversion.engine_versions.time.monotonic", return_value=10.0
    )
    killpg = mocker.patch(
        "markweave.conversion.engine_versions.os.killpg",
        side_effect=ProcessLookupError,
    )

    with pytest.raises(EngineProbeError, match="timeout"):
        probe(tmp_path).observe("pandoc", "pandoc", deadline_monotonic=9.0)

    killpg.assert_called_once_with(4321, 15)
    process.wait.assert_called_once_with()


def test_probe_cancellation_terminates_the_process_group(
    tmp_path: Path, mocker
) -> None:
    process = mocker.Mock(pid=4321)
    process.wait.side_effect = [subprocess.TimeoutExpired("probe", 0.1), 0]
    mocker.patch(
        "markweave.conversion.engine_versions.subprocess.Popen", return_value=process
    )
    killpg = mocker.patch("markweave.conversion.engine_versions.os.killpg")

    with pytest.raises(EngineProbeError) as captured:
        probe(tmp_path).observe(
            "mermaid-cli",
            "mmdc",
            cancellation_requested=mocker.Mock(side_effect=(False, True)),
        )

    assert captured.value.reason == "cancelled"
    killpg.assert_called_once_with(4321, 15)


@pytest.mark.parametrize("failure", ["probe", "wait"])
def test_unexpected_interruption_still_terminates_the_process_group(
    tmp_path: Path, mocker, failure: str
) -> None:
    process = mocker.Mock(pid=4321)
    if failure == "wait":
        process.wait.side_effect = [KeyboardInterrupt, 0]
        cancelled = mocker.Mock(return_value=False)
    else:
        cancelled = mocker.Mock(side_effect=RuntimeError("probe failed"))
    mocker.patch(
        "markweave.conversion.engine_versions.subprocess.Popen", return_value=process
    )
    killpg = mocker.patch("markweave.conversion.engine_versions.os.killpg")

    with pytest.raises((KeyboardInterrupt, RuntimeError)):
        probe(tmp_path).observe("pandoc", "pandoc", cancellation_requested=cancelled)

    killpg.assert_called_once_with(4321, 15)


def test_probe_without_host_path_has_minimal_environment(
    tmp_path: Path, mocker
) -> None:
    _, start = fake_process(mocker, b"11.17.0\n")
    popen = mocker.patch(
        "markweave.conversion.engine_versions.subprocess.Popen", side_effect=start
    )
    EngineVersionProbe(EngineProbeConfig(5.0, 0.5, 0.1, tmp_path), {}).observe(
        "mermaid-cli", "mmdc"
    )
    assert "PATH" not in popen.call_args.kwargs["env"]


@pytest.mark.parametrize(
    "values",
    [(0, 1.0, 1.0), (1.0, float("nan"), 1.0), (1.0, 1.0, -1.0), (True, 1.0, 1.0)],
)
def test_probe_configuration_fails_closed(values: tuple[float, float, float]) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        EngineProbeConfig(*values)
