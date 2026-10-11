"""All-or-nothing publication of local conversion outputs (T106, D1).

Every output is first staged and synchronized in a private temporary file. The
commit then publishes the complete set: new names are hard-linked so concurrent
files are never replaced, and ``--overwrite`` keeps a hard-linked copy of each
original until the whole set is published. Cancellation, the global deadline,
or any failure before the final decision point restores the previous set.
"""

from __future__ import annotations

import fcntl
import os
import stat
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from uuid import uuid4

from markweave.cli.errors import CliError

_OUTPUT_FILE_MODE = 0o600
_TEMPORARY_PREFIX = ".markweave-local-"
_LOCK_POLL_SECONDS = 0.05


@dataclass
class _Staged:
    name: str
    temporary: str
    identity: tuple[int, int]


@dataclass
class _Committed:
    name: str
    identity: tuple[int, int]
    backup: str | None


def output_exists_error() -> CliError:
    return CliError(
        "output_exists",
        "A local output already exists; use --overwrite to replace it.",
    )


def output_failure() -> CliError:
    return CliError(
        "output_write_failed", "The local conversion outputs could not be written."
    )


def check_destination(directory: int, name: str, *, overwrite: bool) -> None:
    """Refuse an existing destination, or a non-regular one when overwriting."""
    try:
        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError:
        raise output_failure() from None
    if not overwrite:
        raise output_exists_error()
    if not stat.S_ISREG(metadata.st_mode):
        raise CliError("output_unsafe", "A local output destination is unsafe.")


def publish_outputs(
    directory: int,
    files: Sequence[tuple[str, bytes]],
    *,
    overwrite: bool,
    checkpoint: Callable[[], None],
    lock_timeout_seconds: float,
) -> None:
    """Publish every file or none; ``checkpoint`` raises to abort and roll back.

    Publications into one directory are serialized with an exclusive lock on the
    directory itself, so an overlapping publication can never observe or undo a
    partially committed set. Waiting honors ``checkpoint`` and is bounded.
    """
    _lock_directory(directory, checkpoint, lock_timeout_seconds)
    try:
        _publish_locked(directory, files, overwrite=overwrite, checkpoint=checkpoint)
    finally:
        with suppress(OSError):
            fcntl.flock(directory, fcntl.LOCK_UN)


def _lock_directory(
    directory: int, checkpoint: Callable[[], None], timeout_seconds: float
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        checkpoint()
        try:
            fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise CliError(
                    "output_busy",
                    "Another local conversion is publishing into the output directory.",
                ) from None
            time.sleep(_LOCK_POLL_SECONDS)
            continue
        except OSError:
            raise output_failure() from None
        return


def _publish_locked(
    directory: int,
    files: Sequence[tuple[str, bytes]],
    *,
    overwrite: bool,
    checkpoint: Callable[[], None],
) -> None:
    staged: list[_Staged] = []
    committed: list[_Committed] = []
    backups: list[str] = []
    succeeded = False
    try:
        for name, content in files:
            checkpoint()
            check_destination(directory, name, overwrite=overwrite)
            staged.append(_stage(directory, name, content))
        checkpoint()
        for item in staged:
            committed.append(_commit(directory, item, overwrite, backups))
            checkpoint()
        _fsync(directory)
        # Final decision point: a later signal no longer withdraws the result.
        checkpoint()
        succeeded = True
    except BaseException:
        _rollback(directory, committed)
        raise
    finally:
        for item in staged:
            with suppress(OSError):
                os.unlink(item.temporary, dir_fd=directory)
        if succeeded:
            for backup in backups:
                with suppress(OSError):
                    os.unlink(backup, dir_fd=directory)
        with suppress(OSError):
            os.fsync(directory)


def _stage(directory: int, name: str, content: bytes) -> _Staged:
    temporary = f"{_TEMPORARY_PREFIX}{uuid4().hex}"
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            _OUTPUT_FILE_MODE,
            dir_fd=directory,
        )
    except OSError:
        raise output_failure() from None
    staged = _Staged(name, temporary, (0, 0))
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
            metadata = os.fstat(output.fileno())
    except OSError:
        with suppress(OSError):
            os.unlink(temporary, dir_fd=directory)
        raise output_failure() from None
    staged.identity = (metadata.st_dev, metadata.st_ino)
    return staged


def _commit(
    directory: int, item: _Staged, overwrite: bool, backups: list[str]
) -> _Committed:
    backup: str | None = None
    try:
        if overwrite and _exists(directory, item.name):
            check_destination(directory, item.name, overwrite=True)
            backup = f"{_TEMPORARY_PREFIX}{uuid4().hex}.previous"
            os.link(
                item.name,
                backup,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            try:
                os.replace(
                    item.temporary,
                    item.name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                )
            except OSError:
                with suppress(OSError):
                    os.unlink(backup, dir_fd=directory)
                raise
            backups.append(backup)
        else:
            os.link(
                item.temporary,
                item.name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
    except FileExistsError:
        raise output_exists_error() from None
    except OSError:
        raise output_failure() from None
    return _Committed(item.name, item.identity, backup)


def _rollback(directory: int, committed: list[_Committed]) -> None:
    """Undo only names that still hold this publication's files.

    A destination whose identity changed belongs to someone else: it is left in
    place, the original copy is kept, and a stable conflict is reported.
    """
    failed = conflicted = False
    for item in reversed(committed):
        try:
            metadata = os.stat(item.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            conflicted = conflicted or item.backup is not None
            continue
        except OSError:
            failed = True
            continue
        if (metadata.st_dev, metadata.st_ino) != item.identity:
            conflicted = conflicted or item.backup is not None
            continue
        try:
            if item.backup is not None:
                os.replace(
                    item.backup, item.name, src_dir_fd=directory, dst_dir_fd=directory
                )
            else:
                os.unlink(item.name, dir_fd=directory)
        except OSError:
            failed = True
    if conflicted:
        raise CliError(
            "output_conflict",
            "A local output changed during rollback; the previous version was kept "
            "in a hidden .markweave-local-*.previous file.",
        )
    if failed:
        raise CliError(
            "output_rollback_failed",
            "Previous local outputs could not be fully restored; inspect the output "
            "directory.",
        )


def _exists(directory: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _fsync(directory: int) -> None:
    try:
        os.fsync(directory)
    except OSError:
        raise output_failure() from None
