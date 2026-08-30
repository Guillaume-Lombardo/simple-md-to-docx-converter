"""Acceptance tests for the durable OpenAPI v1 artifact and compatibility gate."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from scripts.openapi_contract import (
    ARTIFACT,
    build_contract,
    build_contract_app,
    canonical_bytes,
    classify_changes,
    main,
)

T41_FIXTURE = Path("tests/fixtures/t41_http_contract/openapi.json")


def _artifact() -> dict[str, Any]:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def _parameters(
    operation: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (parameter["in"], parameter["name"]): parameter
        for parameter in operation.get("parameters", [])
    }


@pytest.mark.unit
def test_canonical_artifact_matches_generator_runtime_and_t41_fixture() -> None:
    artifact_bytes = ARTIFACT.read_bytes()
    assert artifact_bytes == canonical_bytes(build_contract())
    assert artifact_bytes == canonical_bytes(
        json.loads(T41_FIXTURE.read_text(encoding="utf-8"))
    )

    with TestClient(build_contract_app()) as client:
        runtime_bytes = canonical_bytes(client.get("/openapi.json").json())
    assert runtime_bytes == artifact_bytes


@pytest.mark.unit
def test_artifact_covers_cross_cutting_http_contracts() -> None:
    contract = _artifact()
    paths = contract["paths"]
    schemas = contract["components"]["schemas"]

    conversion_body = schemas["Body_create_conversion_api_v1_conversions_post"]
    assert set(conversion_body["required"]) == {"source", "output"}
    assert {"template_id", "template_version_id"}.issubset(
        conversion_body["properties"]
    )
    assert schemas["ConversionResponse"]["properties"]["template_mode"] == {
        "$ref": "#/components/schemas/TemplateMode"
    }

    for path in ("/api/v1/conversions", "/api/v1/templates", "/api/v1/audit"):
        parameters = _parameters(paths[path]["get"])
        assert ("query", "offset") in parameters
        assert ("query", "limit") in parameters

    mutation = paths["/api/v1/templates/{template_id}"]["patch"]
    assert ("header", "If-Match") in _parameters(mutation)
    assert {"412", "428"}.issubset(mutation["responses"])
    assert (
        "ETag"
        in paths["/api/v1/templates/{template_id}"]["get"]["responses"]["200"][
            "headers"
        ]
    )

    assert schemas["ErrorResponse"]["properties"]["error"] == {
        "$ref": "#/components/schemas/ErrorDetail"
    }
    assert {"401", "403"}.issubset(paths["/api/v1/admin/users"]["get"]["responses"])
    assert {"/health/live", "/health/ready"}.issubset(paths)
    assert set(
        paths["/api/v1/conversions/{job_id}/result"]["get"]["responses"]["200"][
            "content"
        ]
    ) == {"application/octet-stream"}

    restricted_allowed = {
        "/api/v1/session",
        "/api/v1/logout",
        "/api/v1/password",
    }
    assert restricted_allowed.issubset(paths)
    assert "X-CSRF-Token" in {
        name
        for location, name in _parameters(paths["/api/v1/password"]["post"])
        if location == "header"
    }


@pytest.mark.unit
def test_cli_http_endpoints_are_declared_by_durable_artifact() -> None:
    paths = _artifact()["paths"]
    cli_operations = {
        ("post", "/api/v1/login"),
        ("get", "/api/v1/session"),
        ("post", "/api/v1/logout"),
        ("post", "/api/v1/password"),
    }
    assert all(method in paths[path] for method, path in cli_operations)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("category", "mutate"),
    [
        ("route", lambda value: value["paths"].pop("/health/live")),
        (
            "method",
            lambda value: value["paths"]["/health/ready"].pop("get"),
        ),
        (
            "status",
            lambda value: value["paths"]["/health/ready"]["get"]["responses"].pop(
                "503"
            ),
        ),
        (
            "header",
            lambda value: value["paths"]["/health/ready"]["get"]["responses"]["200"][
                "headers"
            ].pop("X-Correlation-ID"),
        ),
        (
            "security",
            lambda value: value["paths"]["/health/ready"]["get"].update(
                security=[{"cookieAuth": []}]
            ),
        ),
        (
            "schema",
            lambda value: value["components"]["schemas"]["ErrorDetail"][
                "properties"
            ].pop("code"),
        ),
        (
            "required-field",
            lambda value: value["components"]["schemas"]["LoginRequest"][
                "required"
            ].append("future_field"),
        ),
    ],
)
def test_compatibility_gate_rejects_every_owned_breaking_change(
    category: str, mutate: Callable[[dict[str, Any]], object]
) -> None:
    baseline = _artifact()
    current = deepcopy(baseline)
    mutate(current)
    changes = classify_changes(baseline, current)
    assert any(
        change.severity == "incompatible" and change.category == category
        for change in changes
    )


@pytest.mark.unit
def test_compatibility_gate_classifies_compatible_additions() -> None:
    baseline = _artifact()
    current = deepcopy(baseline)
    current["paths"]["/api/v1/future"] = {
        "get": {"responses": {"200": {"description": "Future response"}}}
    }
    current["components"]["schemas"]["ErrorDetail"]["properties"]["hint"] = {
        "type": "string"
    }

    changes = classify_changes(baseline, current)
    assert changes
    assert {change.severity for change in changes} == {"compatible"}
    assert {change.category for change in changes} == {"route", "schema"}


@pytest.mark.unit
def test_check_command_rejects_stale_artifact(tmp_path: Path, capsys) -> None:
    artifact = tmp_path / "v1.json"
    artifact.write_text("{}\n", encoding="utf-8")
    assert main(["check", "--artifact", str(artifact)]) == 1
    assert "is stale" in capsys.readouterr().err
