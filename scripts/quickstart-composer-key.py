"""Initialize a private quickstart Composer key only when old data proves safe."""

from __future__ import annotations

import os
import secrets
import sqlite3
import stat
import sys
from contextlib import closing
from pathlib import Path

_KEY_MODE = 0o600
_HEX_KEY_LENGTH = 64
_CLI_ARGC = 4


def ensure_key(
    key_directory: Path, data_directory: Path | None, uid: int, gid: int = 0
) -> None:
    """Keep one key identity across restarts and refuse uncertain restores."""

    key = key_directory / "key"
    try:
        details = key.lstat()
    except FileNotFoundError:
        details = None
    if details is not None:
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_uid != uid
            or stat.S_IMODE(details.st_mode) != _KEY_MODE
            or len(key.read_bytes().removesuffix(b"\n")) != _HEX_KEY_LENGTH
        ):
            raise RuntimeError("The existing Composer key is unsafe or invalid")
        try:
            bytes.fromhex(key.read_bytes().removesuffix(b"\n").decode("ascii"))
        except UnicodeDecodeError, ValueError:
            raise RuntimeError(
                "The existing Composer key is unsafe or invalid"
            ) from None
        return

    if data_directory is not None and not _old_data_has_no_keyed_records(
        data_directory
    ):
        raise RuntimeError("Existing Markweave data needs its original Composer key")
    encoded = secrets.token_hex(32).encode("ascii")
    descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _KEY_MODE)
    try:
        os.write(descriptor, encoded)
        os.fchown(descriptor, uid, gid)
        os.fchmod(descriptor, _KEY_MODE)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = os.open(key_directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _old_data_has_no_keyed_records(data_directory: Path) -> bool:
    database = data_directory / "metadata.sqlite3"
    if not database.is_file() or database.is_symlink():
        return False
    try:
        with closing(
            sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=5)
        ) as db:
            tables = {
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('composer_key_identity', 'composer_credentials')"
                )
            }
            if (
                "composer_key_identity" in tables
                and db.execute("SELECT 1 FROM composer_key_identity LIMIT 1").fetchone()
                is not None
            ):
                return False
            if "composer_credentials" not in tables:
                return True
            return (
                db.execute(
                    "SELECT 1 FROM composer_credentials WHERE api_key IS NOT NULL "
                    "OR client_certificate IS NOT NULL OR client_private_key IS NOT NULL "
                    "OR ca_bundle IS NOT NULL LIMIT 1"
                ).fetchone()
                is None
            )
    except sqlite3.Error:
        return False


def main() -> int:
    if len(sys.argv) != _CLI_ARGC:
        return 2
    key_directory = Path(sys.argv[1])
    data_directory = None if sys.argv[2] == "-" else Path(sys.argv[2])
    try:
        ensure_key(key_directory, data_directory, int(sys.argv[3]))
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
