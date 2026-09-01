"""Typed application configuration loaded fail-closed from the environment."""

from __future__ import annotations

import os
import warnings
from enum import StrEnum
from pathlib import Path
from typing import Any, Self, get_args

from pydantic import (
    AnyHttpUrl,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised without configuration values when startup configuration is invalid."""


class StorageProfile(StrEnum):
    """Coherent persistence profiles supported by the service."""

    STANDALONE = "standalone"
    DISTRIBUTED = "distributed"


class MalwareScanningMode(StrEnum):
    """Operator-selected upload malware-scanning trust boundary."""

    CLAMAV = "clamav"
    TRUSTED_UPSTREAM = "trusted-upstream"


class Settings(BaseSettings):
    """Security-sensitive application settings."""

    model_config = SettingsConfigDict(
        env_prefix="MARKWEAVE_",
        case_sensitive=False,
        extra="ignore",
    )

    host: str = Field(default="0.0.0.0", min_length=1)  # noqa: S104 - container bind
    port: int = Field(default=8080, ge=1, le=65_535)
    initial_admin_username: str = Field(min_length=1)
    initial_admin_password: SecretStr
    user_provisioning_file: Path | None = None
    argon2_memory_cost: int = Field(default=19_456, ge=8)
    argon2_time_cost: int = Field(default=2, ge=1)
    argon2_parallelism: int = Field(default=1, ge=1)
    session_token_bytes: int = Field(default=32, ge=16)
    session_idle_seconds: int = Field(default=30 * 60, ge=1)
    session_absolute_seconds: int = Field(default=8 * 60 * 60, ge=1)
    session_cookie_name: str = Field(default="md_converter_session", min_length=1)
    public_origin: AnyHttpUrl | None = None
    insecure_evaluation_mode: bool = False
    conversion_upload_max_bytes: int = Field(gt=0)
    conversion_request_max_bytes: int = Field(gt=0)
    conversion_max_decompressed_bytes: int = Field(gt=0)
    conversion_max_files: int = Field(gt=0)
    conversion_max_images: int = Field(gt=0)
    conversion_max_diagrams: int = Field(gt=0)
    conversion_max_compression_ratio: float = Field(ge=1.0, allow_inf_nan=False)
    conversion_image_max_source_bytes: int = Field(gt=0)
    conversion_image_max_width_pixels: int = Field(gt=0)
    conversion_image_max_height_pixels: int = Field(gt=0)
    conversion_image_max_pixels: int = Field(gt=0)
    conversion_image_max_svg_elements: int = Field(gt=0)
    conversion_image_max_svg_depth: int = Field(gt=0, le=64)
    conversion_mermaid_max_source_bytes: int = Field(gt=0)
    conversion_mermaid_max_total_source_bytes: int = Field(gt=0)
    conversion_mermaid_max_output_bytes: int = Field(gt=0)
    conversion_mermaid_max_total_output_bytes: int = Field(gt=0)
    conversion_mermaid_max_width_pixels: int = Field(gt=0)
    conversion_mermaid_max_height_pixels: int = Field(gt=0)
    conversion_mermaid_executable: str = Field(min_length=1)
    conversion_chromium_executable: str = Field(min_length=1)
    conversion_pdf_cancellation_poll_seconds: float = Field(gt=0, allow_inf_nan=False)
    conversion_pdf_max_bytes: int = Field(gt=0)
    conversion_pdf_max_decoded_stream_bytes: int = Field(gt=0)
    conversion_pdf_max_pages: int = Field(gt=0)
    conversion_pdf_max_objects: int = Field(gt=0)
    conversion_pdf_max_object_depth: int = Field(gt=0)
    conversion_font_manifest_path: Path
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
    worker_metrics_bind_host: str = Field(
        default="127.0.0.1", min_length=1, max_length=255
    )
    worker_metrics_port: int = Field(default=9464, ge=1, le=65_535)
    worker_metrics_max_connections: int = Field(default=4, gt=0, le=64)
    worker_metrics_observation_limit: int = Field(default=2, gt=0, le=64)
    worker_metrics_accept_queue_size: int = Field(default=8, gt=0, le=128)
    worker_metrics_request_timeout_seconds: float = Field(
        default=2.0, gt=0, allow_inf_nan=False
    )
    template_max_archive_bytes: int = Field(gt=0)
    template_request_max_bytes: int = Field(gt=0)
    template_metadata_request_max_bytes: int = Field(gt=0)
    template_max_name_characters: int = Field(gt=0)
    template_max_description_characters: int = Field(gt=0)
    template_max_entries: int = Field(gt=0)
    template_max_member_bytes: int = Field(gt=0)
    template_max_total_bytes: int = Field(gt=0)
    template_max_compression_ratio: float = Field(ge=1.0, allow_inf_nan=False)
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
    readiness_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    template_engine_workspace_root: Path | None = None
    malware_scanning_mode: MalwareScanningMode = MalwareScanningMode.CLAMAV
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

    @classmethod
    def _environment_values(cls) -> tuple[dict[str, str], dict[str, str], set[str]]:
        """Read canonical and legacy values without giving either alias precedence."""
        environment: dict[str, list[tuple[str, str]]] = {}
        for name, value in os.environ.items():
            environment.setdefault(name.casefold(), []).append((name, value))

        def find(name: str) -> str | None:
            entries = environment.get(name.casefold(), [])
            if len({value for _, value in entries}) > 1:
                raise ConfigurationError("Invalid application configuration")
            return entries[0][1] if entries else None

        canonical: dict[str, str] = {}
        legacy: dict[str, str] = {}
        dual_definitions: set[str] = set()
        for field_name in cls.model_fields:
            suffix = field_name.upper()
            canonical_value = find(f"MARKWEAVE_{suffix}")
            legacy_value = find(f"MD_CONVERTER_{suffix}")
            if canonical_value is not None:
                canonical[field_name] = canonical_value
            if legacy_value is not None:
                legacy[field_name] = legacy_value
            if canonical_value is not None and legacy_value is not None:
                dual_definitions.add(field_name)
        return canonical, legacy, dual_definitions

    @staticmethod
    def _is_secret(annotation: Any) -> bool:
        """Identify values whose aliases must compare byte-for-byte as supplied."""
        if annotation is SecretStr:
            return True
        return any(Settings._is_secret(argument) for argument in get_args(annotation))

    @field_validator("public_origin")
    @classmethod
    def validate_public_origin(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        """Accept only an HTTP origin, not a URL containing resource components."""
        if value is None:
            return None
        if (
            value.username is not None
            or value.password is not None
            or value.path != "/"
            or value.query is not None
            or value.fragment is not None
        ):
            raise ValueError("public origin must contain only scheme, host, and port")
        return value

    @model_validator(mode="after")
    def validate_lifetimes(self) -> Self:
        """Validate cross-field security and resource invariants."""
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
        if (
            self.conversion_mermaid_max_total_source_bytes
            < self.conversion_mermaid_max_source_bytes
            or self.conversion_mermaid_max_total_output_bytes
            < self.conversion_mermaid_max_output_bytes
        ):
            raise ValueError("aggregate Mermaid limits must cover one diagram")
        if any(
            not character.isascii()
            or not character.isprintable()
            or character.isspace()
            or character in "/\\"
            for character in self.worker_metrics_bind_host
        ):
            raise ValueError("worker metrics bind host is invalid")
        if self.worker_metrics_observation_limit > self.worker_metrics_max_connections:
            raise ValueError(
                "worker metrics observation limit must not exceed connection limit"
            )
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
            canonical_values, legacy_values, dual_definitions = (
                cls._environment_values()
            )
            canonical_settings = cls.model_validate(legacy_values | canonical_values)
            legacy_settings = cls.model_validate(canonical_values | legacy_values)
            for field_name in dual_definitions:
                if cls._is_secret(cls.model_fields[field_name].annotation):
                    if canonical_values[field_name] != legacy_values[field_name]:
                        raise ConfigurationError("Invalid application configuration")
                elif getattr(canonical_settings, field_name) != getattr(
                    legacy_settings, field_name
                ):
                    raise ConfigurationError("Invalid application configuration")
            if (
                "session_idle_seconds" in canonical_values
                or "session_idle_seconds" in legacy_values
            ):
                warnings.warn(
                    "SESSION_IDLE_SECONDS is deprecated and does not control the persisted role policy.",
                    FutureWarning,
                    stacklevel=2,
                )
            return canonical_settings
        except ConfigurationError, ValidationError:
            raise ConfigurationError("Invalid application configuration") from None
