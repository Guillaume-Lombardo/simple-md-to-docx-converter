"""Unit coverage for strict credential-bearing startup CSV parsing."""

from pathlib import Path

import pytest

from markweave.auth.models import Role
from markweave.auth.provisioning import load_user_provisioning_csv
from markweave.config import ConfigurationError

HEADER = "username,password,role,active,password_change_required\n"


@pytest.mark.unit
def test_strict_csv_parses_normalized_users_without_persisting_material(
    tmp_path: Path,
) -> None:
    source = tmp_path / "users.csv"
    source.write_text(
        HEADER + "  Alice  ,temporary password,user,true,true\n",
        encoding="utf-8",
    )

    record = load_user_provisioning_csv(source)[0]
    assert record.username == "Alice"
    assert record.normalized_username == "alice"
    assert record.password == "temporary password"  # noqa: S105 - test fixture
    assert record.role is Role.USER
    assert record.active
    assert record.password_change_required


@pytest.mark.unit
@pytest.mark.parametrize(
    "content",
    [
        "username,password\nAlice,secret\n",
        HEADER,
        HEADER + "Alice,,user,true,true\n",
        HEADER + "Alice,secret,owner,true,true\n",
        HEADER + "Alice,secret,user,yes,true\n",
        HEADER + "Alice,secret,user,true,true,unexpected\n",
        HEADER + "Alice,one,user,true,true\n\uff21LICE,two,user,true,true\n",
        HEADER + f"{'A' * 256},secret,user,true,true\n",
        HEADER + "Ali\x00ce,secret,user,true,true\n",
        HEADER + "Alice,sec\x00ret,user,true,true\n",
    ],
)
def test_strict_csv_rejects_invalid_batches_without_reflecting_values(
    tmp_path: Path, content: str
) -> None:
    source = tmp_path / "users.csv"
    source.write_text(content, encoding="utf-8")

    with pytest.raises(ConfigurationError) as caught:
        load_user_provisioning_csv(source)
    assert str(caught.value) == "Invalid user provisioning file"
    assert "secret" not in str(caught.value)


@pytest.mark.unit
def test_strict_csv_rejects_symlinks_and_invalid_utf8(tmp_path: Path) -> None:
    target = tmp_path / "target.csv"
    target.write_bytes(b"\xff")
    link = tmp_path / "users.csv"
    link.symlink_to(target)

    for source in (link, target, tmp_path / "missing.csv"):
        with pytest.raises(ConfigurationError, match="Invalid user provisioning file"):
            load_user_provisioning_csv(source)
