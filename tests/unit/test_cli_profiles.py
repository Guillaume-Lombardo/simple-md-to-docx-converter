"""Security coverage for owner-only local CLI connection profiles."""

from __future__ import annotations

import os
import stat

import pytest

from markweave.cli.errors import CliError
from markweave.cli.profiles import ProfileStore, validate_service_url
from markweave.cli.types import ConnectionProfile

pytestmark = pytest.mark.unit


def _profile(name: str = "default") -> ConnectionProfile:
    return ConnectionProfile(
        name=name,
        service_url="https://converter.example",
        session_state="md_converter_session=session-value",
        csrf_state="csrf-value",
    )


def test_profiles_are_atomic_owner_only_and_redacted(tmp_path) -> None:
    """The persisted document contains the bounded state but no password field."""
    store = ProfileStore(tmp_path)
    store.save(_profile("work"))

    path = tmp_path / "markweave" / "profiles" / "work.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert '"password"' not in path.read_text(encoding="utf-8")
    assert "session-value" not in repr(store.load("work"))
    assert store.load("work") == _profile("work")


@pytest.mark.parametrize("name", ("../escape", "/absolute", "two words", "", "a" * 65))
def test_profile_names_cannot_escape_the_store(tmp_path, name: str) -> None:
    """Profile names never become attacker-controlled filesystem paths."""
    with pytest.raises(CliError, match="Profile names"):
        ProfileStore(tmp_path).save(_profile(name))


def test_symlink_and_permissive_profile_files_are_rejected(tmp_path) -> None:
    """Reading and replacing reject links and unsafe existing file metadata."""
    store = ProfileStore(tmp_path)
    directory = tmp_path / "markweave" / "profiles"
    directory.mkdir(parents=True, mode=0o700)
    target = tmp_path / "outside"
    target.write_text("not a profile", encoding="utf-8")
    (directory / "default.json").symlink_to(target)

    with pytest.raises(CliError, match="unsafe"):
        store.load("default")
    with pytest.raises(CliError, match="unsafe"):
        store.save(_profile())

    (directory / "default.json").unlink()
    unsafe = directory / "default.json"
    unsafe.write_text("{}", encoding="utf-8")
    unsafe.chmod(0o644)
    with pytest.raises(CliError, match="unsafe"):
        store.load("default")


def test_symlinked_profile_directory_is_rejected(tmp_path) -> None:
    """No profile-directory component may redirect state outside the XDG root."""
    state_home = tmp_path / "state"
    state_home.mkdir(mode=0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (state_home / "markweave").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CliError, match="unsafe"):
        ProfileStore(state_home).save(_profile())


def test_hostile_profile_data_and_relative_xdg_home_fail_closed(
    tmp_path, monkeypatch
) -> None:
    """Malformed state and relative XDG locations cannot be interpreted permissively."""
    store = ProfileStore(tmp_path)
    directory = tmp_path / "markweave" / "profiles"
    directory.mkdir(parents=True, mode=0o700)
    path = directory / "default.json"
    path.write_text('{"session_state":"secret"}', encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(CliError, match="invalid"):
        store.load("default")

    monkeypatch.setenv("XDG_STATE_HOME", "relative")
    with pytest.raises(CliError, match="absolute"):
        ProfileStore().save(_profile())


@pytest.mark.parametrize(
    ("url", "code"),
    (
        ("http://converter.example", "tls_required"),
        ("http://localhost.evil.example", "tls_required"),
        ("file:///etc/passwd", "invalid_service_url"),
        ("https://user:password@converter.example", "invalid_service_url"),
        ("https://converter.example/?query", "invalid_service_url"),
    ),
)
def test_service_url_validation_fails_closed(url: str, code: str) -> None:
    """Profiles cannot relax HTTPS or smuggle unrelated URL components."""
    with pytest.raises(CliError) as raised:
        validate_service_url(url, verify_tls=True)
    assert raised.value.code == code


@pytest.mark.parametrize(
    "url", ("http://localhost", "http://127.0.0.1", "http://[::1]")
)
def test_loopback_http_is_the_only_tls_evaluation_exception(url: str) -> None:
    """HTTP is constrained to literal loopback services used by rootless evaluation."""
    assert validate_service_url(url, verify_tls=True) == url


def test_profile_replacement_leaves_no_temporary_file(tmp_path) -> None:
    """Repeated atomic replacement does not retain transient credential copies."""
    store = ProfileStore(tmp_path)
    store.save(_profile())
    store.save(
        ConnectionProfile(
            name="default",
            service_url="https://converter.example",
            session_state="md_converter_session=rotated",
            csrf_state="rotated-csrf",
        )
    )
    names = {path.name for path in (tmp_path / "markweave" / "profiles").iterdir()}
    assert names == {"default.json"}
    assert (
        os.getuid()
        == (tmp_path / "markweave" / "profiles" / "default.json").stat().st_uid
    )
