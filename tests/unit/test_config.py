"""Unit tests for fail-closed application configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from md_converter.config import ConfigurationError, Settings


@pytest.mark.unit
def test_security_defaults_and_secret_redaction() -> None:
    secret = "do-not-" + "print"
    settings = Settings(
        initial_admin_username="admin",
        initial_admin_password=secret,
        storage_profile="standalone",
        standalone_data_directory=Path("/data"),
    )
    assert settings.argon2_memory_cost == 19_456
    assert settings.argon2_time_cost == 2
    assert settings.argon2_parallelism == 1
    assert settings.session_token_bytes == 32
    assert settings.session_idle_seconds == 1_800
    assert settings.session_absolute_seconds == 28_800
    assert secret not in repr(settings)


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"initial_admin_username": " "},
        {"initial_admin_password": ""},
        {"session_token_bytes": 15},
        {"session_idle_seconds": 20, "session_absolute_seconds": 10},
    ],
)
def test_invalid_security_configuration_is_rejected(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "initial_admin_username": "admin",
        "initial_admin_password": "secret",
        "storage_profile": "standalone",
        "standalone_data_directory": "/data",
    }
    values.update(overrides)
    with pytest.raises(ValidationError):
        Settings.model_validate(values)


@pytest.mark.unit
def test_environment_load_failure_has_no_secret_or_field_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MD_CONVERTER_INITIAL_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MD_CONVERTER_INITIAL_ADMIN_PASSWORD", "leak-me-not")
    monkeypatch.setenv("MD_CONVERTER_SESSION_TOKEN_BYTES", "invalid")
    monkeypatch.setenv("MD_CONVERTER_STORAGE_PROFILE", "standalone")
    monkeypatch.setenv("MD_CONVERTER_STANDALONE_DATA_DIRECTORY", "/data")
    with pytest.raises(ConfigurationError) as caught:
        Settings.load()
    assert str(caught.value) == "Invalid application configuration"
    assert "leak-me-not" not in repr(caught.value)


@pytest.mark.unit
def test_environment_load_requires_bootstrap_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MD_CONVERTER_INITIAL_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("MD_CONVERTER_INITIAL_ADMIN_PASSWORD", raising=False)
    with pytest.raises(ConfigurationError, match="Invalid application configuration"):
        Settings.load()


@pytest.mark.unit
@pytest.mark.parametrize(
    "values",
    [
        {},
        {"storage_profile": "standalone"},
        {
            "storage_profile": "standalone",
            "standalone_data_directory": "/data",
            "s3_bucket": "mixed",
        },
        {"storage_profile": "distributed", "s3_bucket": "objects"},
        {
            "storage_profile": "distributed",
            "distributed_database_url": "sqlite:///mixed.db",
            "s3_bucket": "objects",
        },
        {
            "storage_profile": "distributed",
            "distributed_database_url": "postgresql+psycopg://db/app",
            "s3_bucket": "objects",
            "standalone_data_directory": "/data",
        },
        {
            "storage_profile": "distributed",
            "distributed_database_url": "postgresql+psycopg://db/app",
            "s3_bucket": "objects",
            "s3_access_key_id": "incomplete",
        },
        {
            "storage_profile": "distributed",
            "distributed_database_url": "postgresql+psycopg://db/app",
            "s3_bucket": " ",
        },
        {
            "storage_profile": "distributed",
            "distributed_database_url": "postgresql+psycopg://db/app",
            "s3_bucket": "objects",
            "s3_access_key_id": "",
            "s3_secret_access_key": "",
        },
    ],
)
def test_mixed_or_incomplete_storage_profiles_fail_fast(
    values: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                "initial_admin_username": "admin",
                "initial_admin_password": "secret",
                **values,
            }
        )


@pytest.mark.unit
def test_distributed_profile_accepts_aws_or_s3_compatible_credentials() -> None:
    admin_secret = "sec" + "ret"
    s3_secret = "secret" + "-key"
    settings = Settings(
        initial_admin_username="admin",
        initial_admin_password=admin_secret,
        storage_profile="distributed",
        distributed_database_url="postgresql+psycopg://database/app",
        s3_bucket="objects",
        s3_endpoint_url="https://s3.example.test",
        s3_access_key_id="access",
        s3_secret_access_key=s3_secret,
    )
    assert settings.standalone_data_directory is None
    assert s3_secret not in repr(settings)
