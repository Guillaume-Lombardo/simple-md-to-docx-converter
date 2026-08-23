"""Typed application configuration loaded fail-closed from the environment."""

from __future__ import annotations

from typing import Self

from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised without configuration values when startup configuration is invalid."""


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

    @model_validator(mode="after")
    def validate_lifetimes(self) -> Self:
        """Require an absolute lifetime at least as long as the idle lifetime."""
        if self.session_absolute_seconds < self.session_idle_seconds:
            raise ValueError("absolute session lifetime must not be shorter than idle")
        if not self.initial_admin_username.strip():
            raise ValueError("initial administrator username must not be blank")
        if not self.initial_admin_password.get_secret_value():
            raise ValueError("initial administrator password must not be blank")
        return self

    @classmethod
    def load(cls) -> Settings:
        """Load environment settings without exposing invalid values in the exception."""
        try:
            return cls()
        except ValidationError:
            raise ConfigurationError("Invalid application configuration") from None
