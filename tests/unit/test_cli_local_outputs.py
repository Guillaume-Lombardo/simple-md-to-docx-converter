"""Unit coverage for all-or-nothing local output publication (T106, D1)."""

from __future__ import annotations

import json
import os
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from markweave.cli import local_outputs
from markweave.cli.errors import CliError
from markweave.cli.main import main
from markweave.conversion import local as local_module
from markweave.conversion.engine_versions import (
    EngineObservation,
    EngineVersionStatus,
)
from markweave.conversion.local import LocalConversionResult
from markweave.conversion.local_settings import LocalConversionSettings
from tests.settings import local_conversion_settings

pytestmark = pytest.mark.unit

FILES = [("doc.docx", b"new-docx"), ("doc.pdf", b"new-pdf"), ("doc.json", b"new-json")]
OLD = {"doc.docx": b"old-docx", "doc.pdf": b"old-pdf", "doc.json": b"old-json"}
PANDOC = EngineObservation("pandoc", "3.10.2", "3.10.2", EngineVersionStatus.PINNED)


@contextmanager
def opened(path: Path) -> Iterator[int]:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def contents(path: Path) -> dict[str, bytes]:
    return {child.name: child.read_bytes() for child in sorted(path.iterdir())}


def existing(path: Path) -> None:
    for name, content in OLD.items():
        (path / name).write_bytes(content)


def interrupt_after(calls: int, error: CliError) -> Callable[[], None]:
    state = {"count": 0}

    def checkpoint() -> None:
        state["count"] += 1
        if state["count"] > calls:
            raise error

    return checkpoint


CANCELLED = CliError("conversion_cancelled", "The local conversion was cancelled.")
TIMEOUT = CliError("conversion_timeout", "The local conversion timed out.")


def test_new_outputs_are_published_owner_only_without_leftovers(
    tmp_path: Path,
) -> None:
    with opened(tmp_path) as directory:
        local_outputs.publish_outputs(
            directory, FILES, overwrite=False, checkpoint=lambda: None
        )
    assert contents(tmp_path) == dict(FILES)
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in tmp_path.iterdir())


def test_overwrite_replaces_the_complete_set_and_removes_originals(
    tmp_path: Path,
) -> None:
    existing(tmp_path)
    with opened(tmp_path) as directory:
        local_outputs.publish_outputs(
            directory, FILES, overwrite=True, checkpoint=lambda: None
        )
    assert contents(tmp_path) == dict(FILES)


@pytest.mark.parametrize("failing_call", [1, 2, 3])
def test_failed_overwrite_commit_restores_the_previous_complete_set(
    tmp_path: Path, mocker, failing_call: int
) -> None:
    existing(tmp_path)
    original_replace = os.replace
    state = {"count": 0}

    def replace(source: Any, target: Any, **options: Any) -> None:
        if not str(source).endswith(".previous"):
            state["count"] += 1
            if state["count"] == failing_call:
                raise OSError("disk failure")
        original_replace(source, target, **options)

    mocker.patch.object(local_outputs.os, "replace", side_effect=replace)
    with opened(tmp_path) as directory, pytest.raises(CliError) as captured:
        local_outputs.publish_outputs(
            directory, FILES, overwrite=True, checkpoint=lambda: None
        )
    assert captured.value.code == "output_write_failed"
    assert contents(tmp_path) == OLD


@pytest.mark.parametrize("failing_fsync", [1, 2, 3])
def test_staging_failure_keeps_previous_outputs(
    tmp_path: Path, mocker, failing_fsync: int
) -> None:
    existing(tmp_path)
    original_fsync = os.fsync
    state = {"count": 0}

    def fsync(descriptor: int) -> None:
        state["count"] += 1
        if state["count"] == failing_fsync:
            raise OSError("disk failure")
        original_fsync(descriptor)

    mocker.patch.object(local_outputs.os, "fsync", side_effect=fsync)
    with opened(tmp_path) as directory, pytest.raises(CliError) as captured:
        local_outputs.publish_outputs(
            directory, FILES, overwrite=True, checkpoint=lambda: None
        )
    assert captured.value.code == "output_write_failed"
    assert contents(tmp_path) == OLD


def test_failed_directory_sync_rolls_back_new_outputs(tmp_path: Path, mocker) -> None:
    original_fsync = os.fsync
    state = {"count": 0}

    def fsync(descriptor: int) -> None:
        state["count"] += 1
        if state["count"] == len(FILES) + 1:
            raise OSError("directory sync failed")
        original_fsync(descriptor)

    mocker.patch.object(local_outputs.os, "fsync", side_effect=fsync)
    with opened(tmp_path) as directory, pytest.raises(CliError, match="written"):
        local_outputs.publish_outputs(
            directory, FILES, overwrite=False, checkpoint=lambda: None
        )
    assert contents(tmp_path) == {}


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("calls", range(8))
@pytest.mark.parametrize("error", [CANCELLED, TIMEOUT])
def test_interruption_at_every_publication_checkpoint_publishes_nothing_new(
    tmp_path: Path, overwrite: bool, calls: int, error: CliError
) -> None:
    if overwrite:
        existing(tmp_path)
    expected = OLD if overwrite else {}
    with opened(tmp_path) as directory, pytest.raises(CliError) as captured:
        local_outputs.publish_outputs(
            directory,
            FILES,
            overwrite=overwrite,
            checkpoint=interrupt_after(calls, error),
        )
    assert captured.value is error
    assert contents(tmp_path) == expected


def test_publication_after_the_final_decision_point_succeeds(tmp_path: Path) -> None:
    with opened(tmp_path) as directory:
        local_outputs.publish_outputs(
            directory, FILES, overwrite=False, checkpoint=interrupt_after(8, CANCELLED)
        )
    assert contents(tmp_path) == dict(FILES)


def test_destination_created_during_overwrite_is_not_replaced(
    tmp_path: Path, mocker
) -> None:
    original_link = os.link

    def link(source: Any, target: Any, **options: Any) -> None:
        if target == "doc.pdf":
            (tmp_path / "doc.pdf").write_bytes(b"concurrent")
        original_link(source, target, **options)

    mocker.patch.object(local_outputs.os, "link", side_effect=link)
    with opened(tmp_path) as directory, pytest.raises(CliError) as captured:
        local_outputs.publish_outputs(
            directory, FILES, overwrite=True, checkpoint=lambda: None
        )
    assert captured.value.code == "output_exists"
    assert contents(tmp_path) == {"doc.pdf": b"concurrent"}


def test_rollback_never_removes_a_concurrently_replaced_file(tmp_path: Path) -> None:
    def checkpoint() -> None:
        if (tmp_path / "doc.docx").exists():
            (tmp_path / "doc.docx").unlink()
            (tmp_path / "doc.docx").write_bytes(b"someone else")
            raise CANCELLED

    with opened(tmp_path) as directory, pytest.raises(CliError):
        local_outputs.publish_outputs(
            directory, FILES, overwrite=False, checkpoint=checkpoint
        )
    assert contents(tmp_path) == {"doc.docx": b"someone else"}


def test_rollback_tolerates_a_created_file_that_already_vanished(
    tmp_path: Path,
) -> None:
    def checkpoint() -> None:
        if (tmp_path / "doc.docx").exists():
            (tmp_path / "doc.docx").unlink()
            raise CANCELLED

    with opened(tmp_path) as directory, pytest.raises(CliError):
        local_outputs.publish_outputs(
            directory, FILES, overwrite=False, checkpoint=checkpoint
        )
    assert contents(tmp_path) == {}


def test_failed_restore_reports_and_keeps_the_original_copy(
    tmp_path: Path, mocker
) -> None:
    existing(tmp_path)
    original_replace = os.replace

    def replace(source: Any, target: Any, **options: Any) -> None:
        if str(source).endswith(".previous"):
            raise OSError("cannot restore")
        original_replace(source, target, **options)

    mocker.patch.object(local_outputs.os, "replace", side_effect=replace)
    with opened(tmp_path) as directory, pytest.raises(CliError) as captured:
        local_outputs.publish_outputs(
            directory, FILES, overwrite=True, checkpoint=interrupt_after(7, CANCELLED)
        )
    assert captured.value.code == "output_rollback_failed"
    retained = [path for path in tmp_path.iterdir() if path.name.endswith(".previous")]
    assert sorted(path.read_bytes() for path in retained) == sorted(OLD.values())


def test_overwrite_refuses_a_destination_turned_unsafe(tmp_path: Path) -> None:
    (tmp_path / "doc.docx").mkdir()
    with opened(tmp_path) as directory, pytest.raises(CliError) as captured:
        local_outputs.publish_outputs(
            directory, FILES, overwrite=True, checkpoint=lambda: None
        )
    assert captured.value.code == "output_unsafe"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["doc.docx"]


@pytest.fixture
def local_cli(tmp_path: Path, mocker) -> Path:
    def convert(self: Any, request: Any, **options: Any) -> LocalConversionResult:
        return LocalConversionResult(
            dict.fromkeys(request.outputs, b"x" * 10), b"{}", (PANDOC,)
        )

    mocker.patch.object(local_module.LocalConverter, "convert", convert)
    mocker.patch.object(
        LocalConversionSettings, "load", return_value=local_conversion_settings()
    )
    (tmp_path / "out").mkdir()
    (tmp_path / "doc.md").write_text("# Local\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_signal_during_output_writing_cancels_publication(
    local_cli: Path, capsys, mocker, signum: signal.Signals
) -> None:
    existing(local_cli / "out")
    original_fsync = os.fsync
    state = {"sent": False}

    def fsync(descriptor: int) -> None:
        if not state["sent"]:
            state["sent"] = True
            os.kill(os.getpid(), signum)
        original_fsync(descriptor)

    mocker.patch.object(local_outputs.os, "fsync", side_effect=fsync)
    status = main(
        (
            "--json",
            "convert",
            str(local_cli / "doc.md"),
            "--local",
            "--overwrite",
            "--to",
            "docx,pdf",
            "-o",
            str(local_cli / "out"),
        )
    )
    error = json.loads(capsys.readouterr().err)["error"]
    assert (status, error["code"]) == (1, "conversion_cancelled")
    assert contents(local_cli / "out") == OLD


def test_deadline_expiring_during_publication_times_out(
    local_cli: Path, capsys, mocker
) -> None:
    clock = {"now": 0.0}
    mocker.patch(
        "markweave.cli.commands.local_conversion.time.monotonic",
        side_effect=lambda: clock["now"],
    )
    original_fsync = os.fsync

    def fsync(descriptor: int) -> None:
        clock["now"] = 100.0
        original_fsync(descriptor)

    mocker.patch.object(local_outputs.os, "fsync", side_effect=fsync)
    status = main(
        (
            "--json",
            "--timeout",
            "30",
            "convert",
            str(local_cli / "doc.md"),
            "--local",
            "-o",
            str(local_cli / "out"),
        )
    )
    error = json.loads(capsys.readouterr().err)["error"]
    assert (status, error["code"]) == (1, "conversion_timeout")
    assert contents(local_cli / "out") == {}
