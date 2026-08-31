"""Unit tests for fail-closed application configuration."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from markweave.config import ConfigurationError, MalwareScanningMode, Settings
from tests.settings import template_settings

TEMPLATE_REQUIRED_FIELDS = tuple(template_settings())


def _environment_configuration(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        **template_settings(),
        "initial_admin_username": "admin",
        "initial_admin_password": "secret",
        "storage_profile": "standalone",
        "standalone_data_directory": "/data",
        "conversion_upload_max_bytes": 1_000_000,
        "conversion_request_max_bytes": 1_100_000,
        "conversion_retry_after_seconds": 1,
        "job_result_retention_seconds": 3_600,
    }
    values.update(overrides)
    return values


def _set_environment_configuration(
    monkeypatch: pytest.MonkeyPatch, prefix: str, **overrides: object
) -> None:
    for field_name in Settings.model_fields:
        monkeypatch.delenv(f"MARKWEAVE_{field_name.upper()}", raising=False)
        monkeypatch.delenv(f"MD_CONVERTER_{field_name.upper()}", raising=False)
    for field_name, value in _environment_configuration(**overrides).items():
        if value is not None:
            monkeypatch.setenv(f"{prefix}{field_name.upper()}", str(value))


@pytest.mark.unit
def test_security_defaults_and_secret_redaction() -> None:
    secret = "do-not-" + "print"
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password=secret,
        storage_profile="standalone",
        standalone_data_directory=Path("/data"),
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    assert settings.argon2_memory_cost == 19_456
    assert settings.argon2_time_cost == 2
    assert settings.argon2_parallelism == 1
    assert settings.session_token_bytes == 32
    assert settings.session_idle_seconds == 1_800
    assert settings.session_absolute_seconds == 28_800
    assert settings.template_version_retention_seconds == 31_536_000
    assert settings.template_min_retained_versions == 10
    assert settings.audit_retention_seconds == 31_536_000
    assert (settings.clamav_host, settings.clamav_port) == ("127.0.0.1", 3310)
    assert settings.clamav_timeout_seconds == 5
    assert settings.malware_scanning_mode is MalwareScanningMode.CLAMAV
    assert settings.public_origin is None
    assert settings.insecure_evaluation_mode is False
    assert secret not in repr(settings)


@pytest.mark.unit
def test_public_origin_loads_from_environment_and_is_canonicalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "MARKWEAVE_INITIAL_ADMIN_USERNAME": "admin",
        "MARKWEAVE_INITIAL_ADMIN_PASSWORD": "secret",
        "MARKWEAVE_STORAGE_PROFILE": "standalone",
        "MARKWEAVE_STANDALONE_DATA_DIRECTORY": "/data",
        "MARKWEAVE_CONVERSION_UPLOAD_MAX_BYTES": "1000000",
        "MARKWEAVE_CONVERSION_REQUEST_MAX_BYTES": "1100000",
        "MARKWEAVE_CONVERSION_RETRY_AFTER_SECONDS": "1",
        "MARKWEAVE_JOB_RESULT_RETENTION_SECONDS": "3600",
        "MARKWEAVE_PUBLIC_ORIGIN": "https://Converter.Example:8443",
    }
    values.update(
        {
            f"MARKWEAVE_{name.upper()}": value
            for name, value in template_settings().items()
        }
    )
    for name, value in values.items():
        monkeypatch.setenv(name, str(value))

    settings = Settings.load()

    assert str(settings.public_origin) == "https://converter.example:8443/"


@pytest.mark.unit
def test_insecure_evaluation_mode_can_be_explicitly_enabled_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "MARKWEAVE_INITIAL_ADMIN_USERNAME": "admin",
        "MARKWEAVE_INITIAL_ADMIN_PASSWORD": "secret",
        "MARKWEAVE_STORAGE_PROFILE": "standalone",
        "MARKWEAVE_STANDALONE_DATA_DIRECTORY": "/data",
        "MARKWEAVE_CONVERSION_UPLOAD_MAX_BYTES": "1000000",
        "MARKWEAVE_CONVERSION_REQUEST_MAX_BYTES": "1100000",
        "MARKWEAVE_CONVERSION_RETRY_AFTER_SECONDS": "1",
        "MARKWEAVE_JOB_RESULT_RETENTION_SECONDS": "3600",
        "MARKWEAVE_INSECURE_EVALUATION_MODE": "true",
    }
    values.update(
        {
            f"MARKWEAVE_{name.upper()}": value
            for name, value in template_settings().items()
        }
    )
    for name, value in values.items():
        monkeypatch.setenv(name, str(value))

    assert Settings.load().insecure_evaluation_mode is True


@pytest.mark.unit
def test_legacy_environment_aliases_remain_supported_through_0_x(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_environment_configuration(
        monkeypatch, "MD_CONVERTER_", host="127.0.0.1", port=8000
    )

    settings = Settings.load()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.session_cookie_name == "md_converter_session"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field_name", "canonical", "legacy"),
    [
        ("host", "127.0.0.1", "127.0.0.1"),
        ("port", "8080", "08080"),
        ("session_idle_seconds", "1800", "01800"),
        ("insecure_evaluation_mode", "true", "TRUE"),
        (
            "public_origin",
            "https://Converter.Example:8443",
            "https://converter.example:8443",
        ),
        ("standalone_data_directory", "/data", "/data"),
        ("malware_scanning_mode", "clamav", "clamav"),
    ],
)
def test_equal_canonical_and_legacy_aliases_compare_typed_values(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    canonical: str,
    legacy: str,
) -> None:
    _set_environment_configuration(monkeypatch, "MARKWEAVE_")
    monkeypatch.setenv(f"MARKWEAVE_{field_name.upper()}", canonical)
    monkeypatch.setenv(f"MD_CONVERTER_{field_name.upper()}", legacy)

    assert getattr(Settings.load(), field_name) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "field_name",
    ("initial_admin_password", "distributed_database_url", "s3_secret_access_key"),
)
def test_secret_aliases_require_an_exact_raw_match(
    monkeypatch: pytest.MonkeyPatch, field_name: str
) -> None:
    overrides: dict[str, object] = {}
    if field_name != "initial_admin_password":
        overrides = {
            "storage_profile": "distributed",
            "standalone_data_directory": None,
            "distributed_database_url": "postgresql+psycopg://db/app",
            "s3_bucket": "objects",
            "s3_access_key_id": "access",
            "s3_secret_access_key": "secret",
        }
    _set_environment_configuration(monkeypatch, "MARKWEAVE_", **overrides)
    canonical = (
        "postgresql+psycopg://one/app"
        if field_name == "distributed_database_url"
        else "canonical-secret"
    )
    legacy = (
        "postgresql+psycopg://two/app"
        if field_name == "distributed_database_url"
        else f"{canonical}-other"
    )
    monkeypatch.setenv(f"MARKWEAVE_{field_name.upper()}", canonical)
    monkeypatch.setenv(f"MD_CONVERTER_{field_name.upper()}", legacy)

    with pytest.raises(ConfigurationError) as caught:
        Settings.load()

    assert str(caught.value) == "Invalid application configuration"
    assert canonical not in repr(caught.value)


@pytest.mark.unit
def test_unequal_nonsecret_aliases_fail_without_exposing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_environment_configuration(monkeypatch, "MARKWEAVE_")
    monkeypatch.setenv("MARKWEAVE_JOB_GLOBAL_QUEUE_CAPACITY", "10")
    monkeypatch.setenv("MD_CONVERTER_JOB_GLOBAL_QUEUE_CAPACITY", "11")

    with pytest.raises(ConfigurationError) as caught:
        Settings.load()

    assert str(caught.value) == "Invalid application configuration"
    assert "10" not in repr(caught.value)
    assert "11" not in repr(caught.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    "public_origin",
    [
        "ftp://converter.example",
        "https://user:password@converter.example",
        "https://converter.example/app",
        "https://converter.example?tenant=one",
        "https://converter.example#login",
    ],
)
def test_public_origin_rejects_non_origin_urls(public_origin: str) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate(
            {
                **template_settings(),
                "initial_admin_username": "admin",
                "initial_admin_password": "secret",
                "storage_profile": "standalone",
                "standalone_data_directory": "/data",
                "conversion_upload_max_bytes": 1_000_000,
                "conversion_request_max_bytes": 1_100_000,
                "conversion_retry_after_seconds": 1,
                "job_result_retention_seconds": 3_600,
                "public_origin": public_origin,
            }
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"initial_admin_username": " "},
        {"initial_admin_password": ""},
        {"session_token_bytes": 15},
        {"session_idle_seconds": 20, "session_absolute_seconds": 10},
        {"conversion_upload_max_bytes": 0},
        {"conversion_request_max_bytes": 1_000_000},
        {"conversion_retry_after_seconds": 0},
        {"conversion_mermaid_max_total_source_bytes": 99_999},
        {"conversion_mermaid_max_total_output_bytes": 999_999},
        {"job_result_retention_seconds": 0},
        {"template_version_retention_seconds": 0},
        {"template_min_retained_versions": 9},
        {"template_max_compression_ratio": float("inf")},
        {"template_max_compression_ratio": float("nan")},
        {"audit_retention_seconds": 0},
        {"readiness_timeout_seconds": float("inf")},
        {"worker_metrics_bind_host": "bad host/private"},
        {"worker_metrics_port": 0},
        {"worker_metrics_max_connections": 0},
        {
            "worker_metrics_max_connections": 1,
            "worker_metrics_observation_limit": 2,
        },
        {"worker_metrics_accept_queue_size": 0},
        {"worker_metrics_request_timeout_seconds": float("inf")},
        {"clamav_port": 65_536},
        {"clamav_timeout_seconds": float("inf")},
        {"malware_scanning_mode": "disabled"},
    ],
)
def test_invalid_security_configuration_is_rejected(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        **template_settings(),
        "initial_admin_username": "admin",
        "initial_admin_password": "secret",
        "storage_profile": "standalone",
        "standalone_data_directory": "/data",
        "conversion_upload_max_bytes": 1_000_000,
        "conversion_request_max_bytes": 1_100_000,
        "conversion_retry_after_seconds": 1,
        "job_result_retention_seconds": 3_600,
    }
    values.update(overrides)
    with pytest.raises(ValidationError):
        Settings.model_validate(values)


@pytest.mark.unit
@pytest.mark.parametrize("duration", [float("inf"), float("nan")])
def test_template_recovery_duration_must_be_finite(duration: float) -> None:
    values: dict[str, object] = {
        **template_settings(),
        "initial_admin_username": "admin",
        "initial_admin_password": "secret",
        "storage_profile": "standalone",
        "standalone_data_directory": "/data",
        "conversion_upload_max_bytes": 1_000_000,
        "conversion_request_max_bytes": 1_100_000,
        "conversion_retry_after_seconds": 1,
        "job_result_retention_seconds": 3_600,
        "template_pending_publication_stale_seconds": duration,
    }

    with pytest.raises(ValidationError):
        Settings.model_validate(values)


@pytest.mark.unit
@pytest.mark.parametrize("missing", TEMPLATE_REQUIRED_FIELDS)
def test_template_runtime_configuration_has_no_implicit_defaults(
    missing: str,
) -> None:
    values: dict[str, object] = {
        **template_settings(),
        "initial_admin_username": "admin",
        "initial_admin_password": "secret",
        "storage_profile": "standalone",
        "standalone_data_directory": "/data",
        "conversion_upload_max_bytes": 1_000_000,
        "conversion_request_max_bytes": 1_100_000,
        "conversion_retry_after_seconds": 1,
        "job_result_retention_seconds": 3_600,
    }
    del values[missing]
    with pytest.raises(ValidationError):
        Settings.model_validate(values)


@pytest.mark.unit
def test_environment_load_failure_has_no_secret_or_field_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MARKWEAVE_INITIAL_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("MARKWEAVE_INITIAL_ADMIN_PASSWORD", "leak-me-not")
    monkeypatch.setenv("MARKWEAVE_SESSION_TOKEN_BYTES", "invalid")
    monkeypatch.setenv("MARKWEAVE_STORAGE_PROFILE", "standalone")
    monkeypatch.setenv("MARKWEAVE_STANDALONE_DATA_DIRECTORY", "/data")
    with pytest.raises(ConfigurationError) as caught:
        Settings.load()
    assert str(caught.value) == "Invalid application configuration"
    assert "leak-me-not" not in repr(caught.value)


@pytest.mark.unit
def test_environment_load_requires_bootstrap_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MARKWEAVE_INITIAL_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("MARKWEAVE_INITIAL_ADMIN_PASSWORD", raising=False)
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
                **template_settings(),
                "initial_admin_username": "admin",
                "initial_admin_password": "secret",
                "conversion_upload_max_bytes": 1_000_000,
                "conversion_request_max_bytes": 1_100_000,
                "conversion_retry_after_seconds": 1,
                "job_result_retention_seconds": 3_600,
                **values,
            }
        )


@pytest.mark.unit
def test_distributed_profile_accepts_aws_or_s3_compatible_credentials() -> None:
    admin_secret = "sec" + "ret"
    s3_secret = "secret" + "-key"
    settings = Settings(
        **template_settings(),
        initial_admin_username="admin",
        initial_admin_password=admin_secret,
        storage_profile="distributed",
        distributed_database_url="postgresql+psycopg://database/app",
        s3_bucket="objects",
        s3_endpoint_url="https://s3.example.test",
        s3_access_key_id="access",
        s3_secret_access_key=s3_secret,
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=1,
        job_result_retention_seconds=3_600,
    )
    assert settings.standalone_data_directory is None
    assert s3_secret not in repr(settings)
