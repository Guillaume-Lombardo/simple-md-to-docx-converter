"""Validate the harness-side admission-window broker launcher of T141."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Iterator
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, cast

import pytest
from pytest_mock import MockerFixture

from scripts.e2e import admission_window_broker as launcher
from scripts.e2e import reverse_broker

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _restore_umask() -> Iterator[None]:
    # The fixture preparation sets an owner-only process umask.
    previous = os.umask(0o022)
    os.umask(previous)
    yield
    os.umask(previous)


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def test_recorder_counts_admissions_and_reconnections_inside_the_window(
    tmp_path: Path,
) -> None:
    clock = _Clock()
    path = tmp_path / "report.json"
    recorder = launcher.AdmissionRecorder(path, 0.15, clock)
    assert _report(path)["slots"]["_exchanges"] == {
        "acquired": 0,
        "refused": 0,
        "released": 0,
        "immediate": 0,
    }

    recorder.acquired("_exchanges", admitted=True)
    recorder.released("_exchanges")
    clock.now += 0.05
    recorder.acquired("_exchanges", admitted=True)
    recorder.released("_exchanges")
    clock.now += 0.5
    recorder.acquired("_exchanges", admitted=True)
    recorder.acquired("_handlers", admitted=False)

    report = _report(path)
    assert report["schema"] == launcher.REPORT_SCHEMA
    assert report["window_seconds"] == 0.15
    assert report["slots"]["_exchanges"] == {
        "acquired": 3,
        "refused": 0,
        "released": 2,
        "immediate": 1,
    }
    assert report["slots"]["_handlers"]["refused"] == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert not list(tmp_path.glob(".*.tmp"))


def test_windowed_slot_pauses_before_it_frees_the_slot(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    semaphore = BoundedSemaphore(1)
    recorder = launcher.AdmissionRecorder(tmp_path / "report.json", 0.15)
    events: list[str] = []

    def pause(seconds: float) -> None:
        assert seconds == 0.15
        assert not semaphore.acquire(blocking=False)
        events.append("pause")

    spy = mocker.spy(recorder, "released")
    slot = launcher.WindowedSlot("_handlers", semaphore, recorder, 0.15, pause)

    assert slot.acquire(blocking=False)
    assert not slot.acquire(blocking=False)
    slot.release()

    assert events == ["pause"]
    spy.assert_called_once_with("_handlers")
    assert semaphore.acquire(blocking=False)
    counts = _report(tmp_path / "report.json")["slots"]["_handlers"]
    assert counts["acquired"] == 1
    assert counts["refused"] == 1
    assert counts["released"] == 1


def test_configuration_sets_every_admission_limit_to_one(tmp_path: Path) -> None:
    root = tmp_path / "broker"
    reverse_broker.prepare(
        root, "localhost/md-converter-reverse-attempt", "sha256:" + "a" * 64, "10.0.0.2"
    )
    original = json.loads((root / "broker.json").read_text("utf-8"))

    path = launcher.write_configuration(root)

    derived = json.loads(path.read_text("utf-8"))
    assert path == root / launcher.CONFIGURATION_NAME
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for limit in ("max_handlers", "max_handshakes", "max_pending_exchanges"):
        assert derived["transport"][limit] == 1
    for document in (original, derived):
        document["transport"] = {
            key: value
            for key, value in document["transport"].items()
            if key not in {"max_handlers", "max_handshakes", "max_pending_exchanges"}
        }
    assert derived == original
    reverse_broker.load_broker_process_config(path)


@pytest.mark.parametrize(
    "configuration",
    [
        {"transport_kind": "unix", "transport": {}},
        {"transport_kind": "mtls", "transport": None},
    ],
)
def test_configuration_requires_the_mtls_fixture_broker(
    tmp_path: Path, configuration: dict[str, object]
) -> None:
    (tmp_path / "broker.json").write_text(json.dumps(configuration))

    with pytest.raises(RuntimeError, match="mTLS fixture broker"):
        launcher.write_configuration(tmp_path)


def test_release_window_wraps_only_the_completion_slots(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    recorder = launcher.AdmissionRecorder(tmp_path / "report.json", 0.15)
    server_type = launcher.mtls_transport.MtlsBrokerServer
    original = server_type.__init__

    def initialize(server: Any, *_args: object, **_kwargs: object) -> None:
        for slot in ("_handshakes", *launcher.WINDOWED_SLOTS):
            setattr(server, slot, BoundedSemaphore(1))

    mocker.patch.object(server_type, "__init__", initialize)
    uninstall = launcher.install_release_window(recorder, 0.15)
    try:
        server = server_type.__new__(server_type)
        cast("Any", server_type.__init__)(server)
    finally:
        uninstall()

    assert server_type.__init__ is initialize
    assert type(server._handshakes) is BoundedSemaphore
    for slot in launcher.WINDOWED_SLOTS:
        assert type(getattr(server, slot)) is launcher.WindowedSlot
    mocker.stopall()
    assert server_type.__init__ is original


def test_main_runs_the_unmodified_broker_entry_point(
    tmp_path: Path, mocker: MockerFixture
) -> None:
    root = tmp_path / "broker"
    reverse_broker.prepare(
        root, "localhost/md-converter-reverse-attempt", "sha256:" + "a" * 64, "10.0.0.2"
    )
    install = mocker.patch.object(launcher, "install_release_window")
    entry = mocker.patch.object(launcher.broker_process, "main", return_value=0)
    report = tmp_path / "report.json"

    assert launcher.main(["--root", str(root), "--report", str(report)]) == 0

    entry.assert_called_once_with([str(root / launcher.CONFIGURATION_NAME)])
    install.assert_called_once()
    assert _report(report)["schema"] == launcher.REPORT_SCHEMA


def test_main_requires_absolute_paths(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        launcher.main(["--root", "broker", "--report", str(tmp_path / "r.json")])


def test_signal_arguments_select_the_owned_launcher(tmp_path: Path) -> None:
    report = tmp_path / "report.json"

    assert reverse_broker._broker_arguments(tmp_path, None) == [
        "-m",
        "markweave.broker.process",
        str(tmp_path / "broker.json"),
    ]
    assert reverse_broker._broker_arguments(tmp_path, report) == [
        "-m",
        "scripts.e2e.admission_window_broker",
        "--root",
        str(tmp_path),
        "--report",
        str(report),
    ]
    with pytest.raises(ValueError, match="absolute"):
        reverse_broker._broker_arguments(tmp_path, Path("report.json"))
