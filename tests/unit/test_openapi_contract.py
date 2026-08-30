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

    assert contract["components"]["securitySchemes"] == {
        "SessionCookie": {
            "description": "Opaque authenticated Markweave session cookie.",
            "in": "cookie",
            "name": "md_converter_session",
            "type": "apiKey",
        }
    }
    assert contract["security"] == [{"SessionCookie": []}]
    public_operations = {
        ("post", "/api/v1/login"),
        ("get", "/health/live"),
        ("get", "/health/ready"),
        ("get", "/metrics"),
    }
    assert all(
        paths[path][method]["security"] == [] for method, path in public_operations
    )
    assert "security" not in paths["/api/v1/session"]["get"]
    assert "security" not in paths["/api/v1/admin/users"]["get"]

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
            lambda value: value["paths"]["/api/v1/session"]["get"].update(security=[]),
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
def test_request_constraints_and_required_body_are_directionally_incompatible() -> None:
    baseline = _artifact()

    constrained = deepcopy(baseline)
    constrained["components"]["schemas"]["LoginRequest"]["properties"]["username"][
        "minLength"
    ] = 1
    changes = classify_changes(baseline, constrained)
    assert any(
        change.severity == "incompatible"
        and change.location.endswith("LoginRequest/properties/username/minLength")
        and "request validation" in change.message
        for change in changes
    )

    body_added = deepcopy(baseline)
    body_added["paths"]["/health/live"]["get"]["requestBody"] = {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    }
    changes = classify_changes(baseline, body_added)
    assert any(
        change.severity == "incompatible"
        and change.category == "required-field"
        and change.location == "GET /health/live/requestBody"
        for change in changes
    )


@pytest.mark.unit
def test_added_nested_request_schemas_are_incompatible() -> None:
    baseline = _artifact()

    closed_object = deepcopy(baseline)
    closed_object["components"]["schemas"]["LoginRequest"]["additionalProperties"] = (
        False
    )
    assert any(
        change.severity == "incompatible"
        and change.location == "components/schemas/LoginRequest/additionalProperties"
        for change in classify_changes(baseline, closed_object)
    )

    expected_fonts = baseline["components"]["schemas"][
        "Body_create_template_api_v1_templates_post"
    ]["properties"]["expected_fonts"]
    expected_fonts.pop("items")
    constrained_array = deepcopy(baseline)
    constrained_array["components"]["schemas"][
        "Body_create_template_api_v1_templates_post"
    ]["properties"]["expected_fonts"]["items"] = {"type": "string"}
    assert any(
        change.severity == "incompatible"
        and change.location.endswith("properties/expected_fonts/items")
        for change in classify_changes(baseline, constrained_array)
    )


@pytest.mark.unit
def test_request_enum_changes_follow_accepted_input_direction() -> None:
    baseline = _artifact()
    username = baseline["components"]["schemas"]["LoginRequest"]["properties"][
        "username"
    ]
    username["enum"] = ["alice", "bob"]

    narrowed = deepcopy(baseline)
    narrowed["components"]["schemas"]["LoginRequest"]["properties"]["username"][
        "enum"
    ] = ["alice"]
    assert any(
        change.severity == "incompatible"
        and "request enum values removed" in change.message
        for change in classify_changes(baseline, narrowed)
    )

    widened = deepcopy(baseline)
    widened["components"]["schemas"]["LoginRequest"]["properties"]["username"][
        "enum"
    ].append("charlie")
    username_changes = [
        change
        for change in classify_changes(baseline, widened)
        if "LoginRequest/properties/username" in change.location
    ]
    assert username_changes
    assert {change.severity for change in username_changes} == {"compatible"}


@pytest.mark.unit
def test_response_enum_and_required_fields_follow_output_direction() -> None:
    baseline = _artifact()

    constrained = deepcopy(baseline)
    constrained["components"]["schemas"]["ErrorDetail"]["properties"]["code"][
        "minLength"
    ] = 1
    response_constraint_changes = [
        change
        for change in classify_changes(baseline, constrained)
        if change.location.endswith("ErrorDetail/properties/code/minLength")
    ]
    assert response_constraint_changes
    assert {change.severity for change in response_constraint_changes} == {"compatible"}

    unconstrained = deepcopy(constrained)
    unconstrained["components"]["schemas"]["ErrorDetail"]["properties"]["code"].pop(
        "minLength"
    )
    assert any(
        change.severity == "incompatible" and "response validation" in change.message
        for change in classify_changes(constrained, unconstrained)
    )

    narrowed = deepcopy(baseline)
    narrowed["components"]["schemas"]["TemplateMode"]["enum"].remove("versioned")
    template_changes = [
        change
        for change in classify_changes(baseline, narrowed)
        if change.location == "components/schemas/TemplateMode"
    ]
    assert template_changes
    assert {change.severity for change in template_changes} == {"compatible"}

    widened = deepcopy(baseline)
    widened["components"]["schemas"]["TemplateMode"]["enum"].append("future")
    assert any(
        change.severity == "incompatible"
        and "response enum values added" in change.message
        for change in classify_changes(baseline, widened)
    )

    missing_required = deepcopy(baseline)
    error_detail = missing_required["components"]["schemas"]["ErrorDetail"]
    error_detail["required"].remove("code")
    error_detail["properties"].pop("code")
    assert any(
        change.severity == "incompatible"
        and change.category == "required-field"
        and change.location == "components/schemas/ErrorDetail/code"
        and "response field became optional" in change.message
        for change in classify_changes(baseline, missing_required)
    )


@pytest.mark.unit
def test_check_command_rejects_stale_artifact(tmp_path: Path, capsys) -> None:
    artifact = tmp_path / "v1.json"
    artifact.write_text("{}\n", encoding="utf-8")
    assert main(["check", "--artifact", str(artifact)]) == 1
    assert "is stale" in capsys.readouterr().err
