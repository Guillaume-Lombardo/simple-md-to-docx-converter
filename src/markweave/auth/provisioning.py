"""Strict flat-file parsing for startup local-account provisioning."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from markweave.auth.models import Role, normalize_username
from markweave.config import ConfigurationError

CSV_FIELDS = (
    "username",
    "password",
    "role",
    "active",
    "password_change_required",
)


@dataclass(frozen=True, slots=True)
class UserProvisioningInput:
    """Validated CSV row before its plaintext password is hashed."""

    username: str
    normalized_username: str
    password: str
    role: Role
    active: bool
    password_change_required: bool


def load_user_provisioning_csv(path: Path) -> list[UserProvisioningInput]:
    """Read one strict UTF-8 CSV without reflecting credential-bearing failures."""
    try:
        if path.is_symlink() or not path.is_file():
            raise ConfigurationError("Invalid user provisioning file")
        with path.open(encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream, strict=True)
            if tuple(reader.fieldnames or ()) != CSV_FIELDS:
                raise ConfigurationError("Invalid user provisioning file")
            records = [_parse_row(row) for row in reader]
    except ConfigurationError:
        raise
    except OSError, UnicodeError, csv.Error, ValueError:
        raise ConfigurationError("Invalid user provisioning file") from None

    normalized = [record.normalized_username for record in records]
    if not records or len(normalized) != len(set(normalized)):
        raise ConfigurationError("Invalid user provisioning file")
    return records


def _parse_row(row: dict[str, str | None]) -> UserProvisioningInput:
    if set(row) != set(CSV_FIELDS) or any(row[field] is None for field in CSV_FIELDS):
        raise ConfigurationError("Invalid user provisioning file")
    username = str(row["username"]).strip()
    password = str(row["password"])
    normalized = normalize_username(username)
    if not normalized or not password:
        raise ConfigurationError("Invalid user provisioning file")
    try:
        role = Role(str(row["role"]))
    except ValueError:
        raise ConfigurationError("Invalid user provisioning file") from None
    return UserProvisioningInput(
        username=username,
        normalized_username=normalized,
        password=password,
        role=role,
        active=_boolean(row["active"]),
        password_change_required=_boolean(row["password_change_required"]),
    )


def _boolean(value: str | None) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ConfigurationError("Invalid user provisioning file")
