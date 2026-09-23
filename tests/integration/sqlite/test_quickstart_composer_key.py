"""Quickstart never replaces a key over existing encrypted Composer data."""

import os
import runpy
import sqlite3
import stat
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.light_coverage]


def _ensure_key() -> Callable[[Path, Path | None, int, int], None]:
    source = (
        Path(__file__).resolve().parents[3] / "scripts" / "quickstart-composer-key.py"
    )
    return runpy.run_path(str(source))["ensure_key"]


def test_fresh_key_is_private_and_reused(tmp_path: Path) -> None:
    key_directory = tmp_path / "secrets"
    key_directory.mkdir()
    ensure_key = _ensure_key()
    ensure_key(key_directory, None, os.getuid(), os.getgid())
    key = key_directory / "key"
    first = key.read_bytes()
    assert len(first) == 64
    assert stat.S_IMODE(key.stat().st_mode) == 0o600
    ensure_key(key_directory, None, os.getuid(), os.getgid())
    assert key.read_bytes() == first
    key.write_bytes(b"invalid")
    with pytest.raises(RuntimeError, match="unsafe or invalid"):
        ensure_key(key_directory, None, os.getuid(), os.getgid())


def test_existing_precomposer_data_can_add_first_key(tmp_path: Path) -> None:
    key_directory = tmp_path / "secrets"
    data_directory = tmp_path / "data"
    key_directory.mkdir()
    data_directory.mkdir()
    with closing(sqlite3.connect(data_directory / "metadata.sqlite3")) as database:
        database.execute("CREATE TABLE users (id TEXT PRIMARY KEY)")
    _ensure_key()(key_directory, data_directory, os.getuid(), os.getgid())
    assert (key_directory / "key").is_file()


def test_empty_migrated_composer_tables_can_add_first_key(tmp_path: Path) -> None:
    key_directory = tmp_path / "secrets"
    data_directory = tmp_path / "data"
    key_directory.mkdir()
    data_directory.mkdir()
    with closing(sqlite3.connect(data_directory / "metadata.sqlite3")) as database:
        database.execute(
            "CREATE TABLE composer_key_identity (id INTEGER, fingerprint TEXT)"
        )
        database.execute(
            "CREATE TABLE composer_credentials (api_key BLOB, client_certificate BLOB, client_private_key BLOB, ca_bundle BLOB)"
        )
    _ensure_key()(key_directory, data_directory, os.getuid(), os.getgid())
    assert (key_directory / "key").is_file()


@pytest.mark.parametrize("table", ["composer_credentials", "composer_key_identity"])
def test_existing_keyed_data_refuses_new_key(tmp_path: Path, table: str) -> None:
    key_directory = tmp_path / "secrets"
    data_directory = tmp_path / "data"
    key_directory.mkdir()
    data_directory.mkdir()
    with closing(sqlite3.connect(data_directory / "metadata.sqlite3")) as database:
        if table == "composer_credentials":
            database.execute(
                "CREATE TABLE composer_credentials (api_key BLOB, client_certificate BLOB, client_private_key BLOB, ca_bundle BLOB)"
            )
            database.execute(
                "INSERT INTO composer_credentials (api_key) VALUES (?)", (b"encrypted",)
            )
        else:
            database.execute(
                "CREATE TABLE composer_key_identity (id INTEGER, fingerprint TEXT)"
            )
            database.execute(
                "INSERT INTO composer_key_identity VALUES (1, 'fingerprint')"
            )
        database.commit()
    with pytest.raises(RuntimeError, match="original Composer key"):
        _ensure_key()(key_directory, data_directory, os.getuid(), os.getgid())
    assert not (key_directory / "key").exists()
