"""Typed application configuration loaded fail-closed from the environment."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised without configuration values when startup configuration is invalid."""


class StorageProfile(StrEnum):
    """Coherent persistence profiles supported by the service."""

    STANDALONE = "standalone"
    DISTRIBUTED = "distributed"


class Settings(BaseSettings):
    """Security-sensitive application settings."""

    model_config = SettingsConfigDict(
        env_prefix="MD_CONVERTER_",
        case_sensitive=False,
        extra="ignore",
    )

    initial_admin_username: str = Field(min_length=1)
    initial_admin_password: SecretStr
    argon2_memory_cost: int = Field(default=19_456, ge=8)
    argon2_time_cost: int = Field(default=2, ge=1)
    argon2_parallelism: int = Field(default=1, ge=1)
    session_token_bytes: int = Field(default=32, ge=16)
    session_idle_seconds: int = Field(default=30 * 60, ge=1)
    session_absolute_seconds: int = Field(default=8 * 60 * 60, ge=1)
    session_cookie_name: str = Field(default="md_converter_session", min_length=1)
    conversion_upload_max_bytes: int = Field(gt=0)
    conversion_request_max_bytes: int = Field(gt=0)
    conversion_max_decompressed_bytes: int = Field(gt=0)
    conversion_max_files: int = Field(gt=0)
    conversion_max_images: int = Field(gt=0)
    conversion_max_diagrams: int = Field(gt=0)
    conversion_retry_after_seconds: int = Field(gt=0)
    job_result_retention_seconds: int = Field(gt=0)
    job_active_limit_per_user: int = Field(gt=0)
    job_global_queue_capacity: int = Field(gt=0)
    job_max_duration_seconds: float = Field(gt=0, allow_inf_nan=False)
    worker_memory_budget_bytes: int = Field(gt=0)
    worker_ephemeral_storage_budget_bytes: int = Field(gt=0)
    worker_lease_seconds: float = Field(gt=0, allow_inf_nan=False)
    worker_heartbeat_seconds: float = Field(gt=0, allow_inf_nan=False)
    worker_incomplete_submission_seconds: float = Field(gt=0, allow_inf_nan=False)
    worker_idle_poll_seconds: float = Field(gt=0, allow_inf_nan=False)
    worker_error_backoff_seconds: float = Field(gt=0, allow_inf_nan=False)
    worker_cleanup_interval_seconds: float = Field(gt=0, allow_inf_nan=False)
    worker_cleanup_batch_size: int = Field(gt=0)
    template_max_archive_bytes: int = Field(gt=0)
    template_request_max_bytes: int = Field(gt=0)
    template_metadata_request_max_bytes: int = Field(gt=0)
    template_max_name_characters: int = Field(gt=0)
    template_max_description_characters: int = Field(gt=0)
    template_max_entries: int = Field(gt=0)
    template_max_member_bytes: int = Field(gt=0)
    template_max_total_bytes: int = Field(gt=0)
    template_max_compression_ratio: float = Field(ge=1.0)
    template_max_xml_elements: int = Field(gt=0)
    template_max_xml_depth: int = Field(gt=0)
    template_max_xml_attributes: int = Field(gt=0)
    template_max_declared_fonts: int = Field(gt=0)
    template_max_font_name_characters: int = Field(gt=0)
    template_pandoc_executable: str = Field(min_length=1)
    template_libreoffice_executable: str = Field(min_length=1)
    template_engine_timeout_seconds: float = Field(gt=0)
    template_engine_termination_grace_seconds: float = Field(gt=0)
    template_pending_publication_stale_seconds: float = Field(gt=0, allow_inf_nan=False)
    template_version_retention_seconds: int = Field(default=365 * 24 * 60 * 60, gt=0)
    template_min_retained_versions: int = Field(default=10, ge=10)
    audit_retention_seconds: int = Field(default=365 * 24 * 60 * 60, gt=0)
    template_engine_workspace_root: Path | None = None
    clamav_host: str = Field(default="127.0.0.1", min_length=1)
    clamav_port: int = Field(default=3310, ge=1, le=65_535)
    clamav_timeout_seconds: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    storage_profile: StorageProfile
    standalone_data_directory: Path | None = None
    distributed_database_url: SecretStr | None = None
    s3_bucket: str | None = Field(default=None, min_length=1)
    s3_endpoint_url: str | None = Field(default=None, min_length=1)
    s3_region: str | None = Field(default=None, min_length=1)
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_lifetimes(self) -> Self:
        """Require an absolute lifetime at least as long as the idle lifetime."""
        if self.session_absolute_seconds < self.session_idle_seconds:
            raise ValueError("absolute session lifetime must not be shorter than idle")
        if not self.initial_admin_username.strip():
            raise ValueError("initial administrator username must not be blank")
        if not self.initial_admin_password.get_secret_value():
            raise ValueError("initial administrator password must not be blank")
        if self.conversion_request_max_bytes <= self.conversion_upload_max_bytes:
            raise ValueError("conversion request limit must exceed the source limit")
        if self.template_request_max_bytes <= self.template_max_archive_bytes:
            raise ValueError("template request limit must exceed the archive limit")
        if self.worker_heartbeat_seconds >= self.worker_lease_seconds:
            raise ValueError("worker heartbeat must be shorter than its lease")
        self._validate_storage_profile()
        return self

    def _validate_storage_profile(self) -> None:
        s3_values = (
            self.s3_bucket,
            self.s3_endpoint_url,
            self.s3_region,
            self.s3_access_key_id,
            self.s3_secret_access_key,
        )
        if self.storage_profile is StorageProfile.STANDALONE:
            if self.standalone_data_directory is None:
                raise ValueError("standalone profile requires its data directory")
            if self.distributed_database_url is not None or any(s3_values):
                raise ValueError("standalone profile cannot use distributed settings")
            return
        if self.standalone_data_directory is not None:
            raise ValueError("distributed profile cannot use standalone settings")
        if (
            self.distributed_database_url is None
            or self.s3_bucket is None
            or not self.s3_bucket.strip()
        ):
            raise ValueError("distributed profile settings are incomplete")
        url = self.distributed_database_url.get_secret_value()
        if not url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise ValueError("distributed profile requires PostgreSQL")
        if (self.s3_access_key_id is None) != (self.s3_secret_access_key is None):
            raise ValueError("S3 static credentials must be configured together")
        if self.s3_access_key_id is not None and not (
            self.s3_access_key_id.get_secret_value()
            and self.s3_secret_access_key is not None
            and self.s3_secret_access_key.get_secret_value()
        ):
            raise ValueError("S3 static credentials must not be blank")

    @classmethod
    def load(cls) -> Settings:
        """Load environment settings without exposing invalid values in the exception."""
        try:
            return cls()
        except ValidationError:
            raise ConfigurationError("Invalid application configuration") from None
