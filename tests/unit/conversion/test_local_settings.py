"""Unit tests for local conversion configuration (T106, D1)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from markweave.config import Settings
from markweave.conversion.local_settings import (
    LocalConfigurationError,
    LocalConversionSettings,
)

pytestmark = pytest.mark.unit

EXAMPLE = Path("examples/local-conversion.env")
OPTIONAL_LOCAL_FIELDS = {
    "conversion_font_manifest_path",
    "template_engine_workspace_root",
}


def example_values() -> dict[str, str]:
    values: dict[str, str] = {}
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, value = line.split("=", 1)
            values[name] = value
    return values


@pytest.fixture
def local_environment(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    for name in list(os.environ):
        if name.upper().startswith(("MARKWEAVE_", "MD_CONVERTER_")):
            monkeypatch.delenv(name)
    values = example_values()
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return values


def test_every_local_field_reuses_the_service_name_and_constraints() -> None:
    service = Settings.model_fields
    for name, field in LocalConversionSettings.model_fields.items():
        assert name in service
        if name == "conversion_font_manifest_path":
            assert field.annotation == Path | None
            assert field.default is None
            continue
        assert field.annotation == service[name].annotation, name
        assert repr(field.metadata) == repr(service[name].metadata), name
        assert field.is_required() == service[name].is_required(), name
    assert {
        name
        for name in LocalConversionSettings.model_fields
        if name.startswith(("job_", "worker_", "storage_", "clamav_", "initial_"))
    } == set()


def test_example_environment_matches_quickstart_values_and_covers_requirements() -> (
    None
):
    compose = Path("compose.yaml").read_text(encoding="utf-8")
    quickstart = dict(
        re.findall(r'^\s+(MARKWEAVE_[A-Z0-9_]+):\s*"?([^"\n]*)"?$', compose, re.M)
    )
    values = example_values()
    assert values == {name: quickstart[name] for name in values}
    required = {
        f"MARKWEAVE_{name.upper()}"
        for name in LocalConversionSettings.model_fields
        if name not in OPTIONAL_LOCAL_FIELDS
    }
    assert required <= values.keys()
    assert values.keys() <= {
        f"MARKWEAVE_{name.upper()}" for name in LocalConversionSettings.model_fields
    }


def test_load_reads_the_service_variables(local_environment: dict[str, str]) -> None:
    settings = LocalConversionSettings.load()
    assert settings.conversion_upload_max_bytes == int(
        local_environment["MARKWEAVE_CONVERSION_UPLOAD_MAX_BYTES"]
    )
    assert settings.conversion_font_manifest_path == Path(
        "/opt/md-converter/font-manifest.json"
    )
    assert settings.template_engine_workspace_root is None


def test_load_accepts_matching_legacy_aliases(
    local_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MARKWEAVE_CONVERSION_MAX_FILES")
    monkeypatch.setenv("MD_CONVERTER_CONVERSION_MAX_FILES", "7")
    monkeypatch.setenv("MD_CONVERTER_CONVERSION_MAX_IMAGES", "50")
    settings = LocalConversionSettings.load()
    assert (settings.conversion_max_files, settings.conversion_max_images) == (7, 50)


def test_missing_and_invalid_variables_are_named_without_values(
    local_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MARKWEAVE_CONVERSION_MAX_FILES")
    monkeypatch.setenv("MARKWEAVE_TEMPLATE_ENGINE_TIMEOUT_SECONDS", "secret-value")
    with pytest.raises(LocalConfigurationError) as captured:
        LocalConversionSettings.load()
    assert captured.value.variables == (
        "MARKWEAVE_CONVERSION_MAX_FILES",
        "MARKWEAVE_TEMPLATE_ENGINE_TIMEOUT_SECONDS",
    )
    assert "secret-value" not in repr(captured.value.__dict__)
    assert captured.value.__cause__ is None


def test_conflicting_aliases_are_named(
    local_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MD_CONVERTER_CONVERSION_MAX_FILES", "99")
    with pytest.raises(LocalConfigurationError) as captured:
        LocalConversionSettings.load()
    assert captured.value.variables == ("MARKWEAVE_CONVERSION_MAX_FILES",)


def test_aggregate_mermaid_invariant_is_enforced(
    local_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKWEAVE_CONVERSION_MERMAID_MAX_TOTAL_OUTPUT_BYTES", "1")
    with pytest.raises(LocalConfigurationError) as captured:
        LocalConversionSettings.load()
    assert captured.value.variables == (
        "MARKWEAVE_CONVERSION_MERMAID_MAX_TOTAL_OUTPUT_BYTES",
        "MARKWEAVE_CONVERSION_MERMAID_MAX_TOTAL_SOURCE_BYTES",
    )


def test_local_settings_ignore_service_only_configuration(
    local_environment: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKWEAVE_STORAGE_PROFILE", "invalid")
    monkeypatch.setenv("MARKWEAVE_INITIAL_ADMIN_PASSWORD", "")
    assert LocalConversionSettings.load().conversion_max_images == 50
