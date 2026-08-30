"""Generate and compatibility-check Markweave's canonical OpenAPI contract."""

# ruff: noqa: PERF401 - compatibility traversal appends conditional classifications

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from fastapi import FastAPI
from pydantic import SecretStr

from markweave.app import create_app
from markweave.config import Settings
from markweave.http.components import AppComponents

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from markweave.auth.ports import ReadinessProbe
    from markweave.auth.service import AuthenticationService
    from markweave.jobs.service import JobService
    from markweave.storage import ObjectStore

ARTIFACT = Path("openapi/v1.json")
HTTP_METHODS = frozenset(
    {"delete", "get", "head", "options", "patch", "post", "put", "trace"}
)
_NON_SECRET_PLACEHOLDER = "contract-generation-only"  # noqa: S105


class _ContractAuthentication:
    """Avoid persistence and password hashing while routes are assembled."""

    def bootstrap_admin(self, username: str, password: str) -> None:
        del username, password


def _contract_settings() -> Settings:
    """Return the minimal deterministic configuration observed by HTTP assembly."""
    return Settings.model_construct(
        initial_admin_username="openapi-contract",
        initial_admin_password=SecretStr(_NON_SECRET_PLACEHOLDER),
        conversion_upload_max_bytes=1_000_000,
        conversion_request_max_bytes=1_100_000,
        conversion_retry_after_seconds=2,
        template_max_archive_bytes=1_000_000,
        template_request_max_bytes=1_100_000,
        template_metadata_request_max_bytes=4_096,
        template_max_name_characters=100,
        template_max_description_characters=1_000,
        session_cookie_name="markweave_contract_session",
        public_origin=None,
        insecure_evaluation_mode=False,
    )


def build_contract_app() -> FastAPI:
    """Assemble the application schema without runtime infrastructure."""
    components = AppComponents(
        authentication=cast("AuthenticationService", _ContractAuthentication()),
        readiness=cast("ReadinessProbe", object()),
        object_store=cast("ObjectStore", object()),
        jobs=cast("JobService", object()),
    )
    return create_app(_contract_settings(), components=components)


def build_contract() -> dict[str, Any]:
    """Generate the schema from the application without runtime infrastructure."""
    schema = build_contract_app().openapi()
    encoded = canonical_bytes(schema)
    if _NON_SECRET_PLACEHOLDER.encode() in encoded:
        raise RuntimeError("canonical OpenAPI contains generation-only configuration")
    return schema


def canonical_bytes(document: Mapping[str, Any]) -> bytes:
    """Encode one OpenAPI document using the repository's canonical normalization."""
    return (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()


def load_document(content: bytes, *, source: str) -> dict[str, Any]:
    """Load a JSON object or fail with a source-specific diagnostic."""
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{source} is not valid UTF-8 JSON: {error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"{source} must contain a JSON object")
    return document


@dataclass(frozen=True, order=True)
class Change:
    """One classified OpenAPI change."""

    severity: Literal["compatible", "incompatible"]
    category: str
    location: str
    message: str


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _schema_changes(  # noqa: PLR0912 - mirrors JSON Schema compatibility sections
    baseline: Mapping[str, Any], current: Mapping[str, Any], location: str
) -> list[Change]:
    changes: list[Change] = []
    for keyword in ("type", "format", "$ref"):
        if keyword in baseline and current.get(keyword) != baseline[keyword]:
            changes.append(
                Change(
                    "incompatible",
                    "schema",
                    location,
                    f"{keyword} changed from {baseline[keyword]!r} to {current.get(keyword)!r}",
                )
            )
    for keyword in (
        "allOf",
        "anyOf",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "not",
        "oneOf",
        "pattern",
    ):
        if keyword in baseline and current.get(keyword) != baseline[keyword]:
            changes.append(
                Change(
                    "incompatible",
                    "schema",
                    f"{location}/{keyword}",
                    f"validation keyword {keyword} changed",
                )
            )
    old_enum = baseline.get("enum")
    new_enum = current.get("enum")
    if isinstance(old_enum, list) and isinstance(new_enum, list):
        removed = sorted(set(old_enum) - set(new_enum), key=repr)
        if removed:
            changes.append(
                Change(
                    "incompatible",
                    "schema",
                    location,
                    f"enum values removed: {removed!r}",
                )
            )

    old_required = set(baseline.get("required", []))
    new_required = set(current.get("required", []))
    for name in sorted(new_required - old_required):
        changes.append(
            Change(
                "incompatible",
                "required-field",
                f"{location}/{name}",
                "field became required",
            )
        )
    for name in sorted(old_required - new_required):
        changes.append(
            Change(
                "compatible",
                "required-field",
                f"{location}/{name}",
                "field became optional",
            )
        )

    old_properties = _mapping(baseline.get("properties"))
    new_properties = _mapping(current.get("properties"))
    for name in sorted(old_properties.keys() - new_properties.keys()):
        changes.append(
            Change(
                "incompatible",
                "schema",
                f"{location}/properties/{name}",
                "property removed",
            )
        )
    for name in sorted(new_properties.keys() - old_properties.keys()):
        severity: Literal["compatible", "incompatible"] = (
            "incompatible" if name in new_required else "compatible"
        )
        changes.append(
            Change(
                severity, "schema", f"{location}/properties/{name}", "property added"
            )
        )
    for name in sorted(old_properties.keys() & new_properties.keys()):
        changes.extend(
            _schema_changes(
                _mapping(old_properties[name]),
                _mapping(new_properties[name]),
                f"{location}/properties/{name}",
            )
        )

    for keyword in ("items", "additionalProperties"):
        old_child = baseline.get(keyword)
        new_child = current.get(keyword)
        if isinstance(old_child, dict) and isinstance(new_child, dict):
            changes.extend(
                _schema_changes(old_child, new_child, f"{location}/{keyword}")
            )
        elif old_child != new_child and old_child is not None:
            changes.append(
                Change(
                    "incompatible",
                    "schema",
                    f"{location}/{keyword}",
                    f"{keyword} changed",
                )
            )
    return changes


def _parameter_key(parameter: object) -> tuple[str, str] | None:
    value = _mapping(parameter)
    location = value.get("in")
    name = value.get("name")
    return (
        (location, name)
        if isinstance(location, str) and isinstance(name, str)
        else None
    )


def _operation_changes(  # noqa: PLR0912 - mirrors OpenAPI operation sections
    baseline: Mapping[str, Any], current: Mapping[str, Any], location: str
) -> list[Change]:
    changes: list[Change] = []
    if baseline.get("security") != current.get("security"):
        changes.append(
            Change(
                "incompatible", "security", location, "security requirements changed"
            )
        )

    old_parameters = {
        key: _mapping(parameter)
        for parameter in baseline.get("parameters", [])
        if (key := _parameter_key(parameter)) is not None
    }
    new_parameters = {
        key: _mapping(parameter)
        for parameter in current.get("parameters", [])
        if (key := _parameter_key(parameter)) is not None
    }
    for key in sorted(old_parameters.keys() - new_parameters.keys()):
        changes.append(
            Change(
                "incompatible",
                "schema",
                f"{location}/parameters/{key[0]}/{key[1]}",
                "parameter removed",
            )
        )
    for key in sorted(new_parameters.keys() - old_parameters.keys()):
        severity: Literal["compatible", "incompatible"] = (
            "incompatible" if new_parameters[key].get("required") else "compatible"
        )
        changes.append(
            Change(
                severity,
                "schema",
                f"{location}/parameters/{key[0]}/{key[1]}",
                "parameter added",
            )
        )
    for key in sorted(old_parameters.keys() & new_parameters.keys()):
        old_parameter = old_parameters[key]
        new_parameter = new_parameters[key]
        parameter_location = f"{location}/parameters/{key[0]}/{key[1]}"
        if not old_parameter.get("required") and new_parameter.get("required"):
            changes.append(
                Change(
                    "incompatible",
                    "required-field",
                    parameter_location,
                    "parameter became required",
                )
            )
        changes.extend(
            _schema_changes(
                _mapping(old_parameter.get("schema")),
                _mapping(new_parameter.get("schema")),
                parameter_location,
            )
        )

    old_body = _mapping(baseline.get("requestBody"))
    new_body = _mapping(current.get("requestBody"))
    if old_body and not new_body:
        changes.append(
            Change(
                "incompatible",
                "schema",
                f"{location}/requestBody",
                "request body removed",
            )
        )
    elif old_body:
        if not old_body.get("required") and new_body.get("required"):
            changes.append(
                Change(
                    "incompatible",
                    "required-field",
                    f"{location}/requestBody",
                    "request body became required",
                )
            )
        for media_type, old_media in _mapping(old_body.get("content")).items():
            new_media = _mapping(_mapping(new_body.get("content")).get(media_type))
            if not new_media:
                changes.append(
                    Change(
                        "incompatible",
                        "schema",
                        f"{location}/requestBody/{media_type}",
                        "request media type removed",
                    )
                )
            else:
                changes.extend(
                    _schema_changes(
                        _mapping(_mapping(old_media).get("schema")),
                        _mapping(new_media.get("schema")),
                        f"{location}/requestBody/{media_type}",
                    )
                )

    old_responses = _mapping(baseline.get("responses"))
    new_responses = _mapping(current.get("responses"))
    for status in sorted(old_responses.keys() - new_responses.keys()):
        changes.append(
            Change(
                "incompatible",
                "status",
                f"{location}/responses/{status}",
                "response status removed",
            )
        )
    for status in sorted(new_responses.keys() - old_responses.keys()):
        changes.append(
            Change(
                "compatible",
                "status",
                f"{location}/responses/{status}",
                "response status added",
            )
        )
    for status in sorted(old_responses.keys() & new_responses.keys()):
        old_response = _mapping(old_responses[status])
        new_response = _mapping(new_responses[status])
        response_location = f"{location}/responses/{status}"
        old_headers = _mapping(old_response.get("headers"))
        new_headers = _mapping(new_response.get("headers"))
        for name in sorted(old_headers.keys() - new_headers.keys()):
            changes.append(
                Change(
                    "incompatible",
                    "header",
                    f"{response_location}/headers/{name}",
                    "response header removed",
                )
            )
        for name in sorted(new_headers.keys() - old_headers.keys()):
            changes.append(
                Change(
                    "compatible",
                    "header",
                    f"{response_location}/headers/{name}",
                    "response header added",
                )
            )
        for name in sorted(old_headers.keys() & new_headers.keys()):
            changes.extend(
                _schema_changes(
                    _mapping(_mapping(old_headers[name]).get("schema")),
                    _mapping(_mapping(new_headers[name]).get("schema")),
                    f"{response_location}/headers/{name}",
                )
            )
        old_content = _mapping(old_response.get("content"))
        new_content = _mapping(new_response.get("content"))
        for media_type in sorted(old_content.keys() - new_content.keys()):
            changes.append(
                Change(
                    "incompatible",
                    "schema",
                    f"{response_location}/content/{media_type}",
                    "response media type removed",
                )
            )
        for media_type in sorted(old_content.keys() & new_content.keys()):
            changes.extend(
                _schema_changes(
                    _mapping(_mapping(old_content[media_type]).get("schema")),
                    _mapping(_mapping(new_content[media_type]).get("schema")),
                    f"{response_location}/content/{media_type}",
                )
            )
    return changes


def classify_changes(  # noqa: PLR0912 - mirrors OpenAPI compatibility sections
    baseline: Mapping[str, Any], current: Mapping[str, Any]
) -> list[Change]:
    """Classify compatibility-relevant differences between two contracts."""
    changes: list[Change] = []
    if baseline.get("security") != current.get("security"):
        changes.append(
            Change(
                "incompatible",
                "security",
                "security",
                "global security requirements changed",
            )
        )
    old_paths = _mapping(baseline.get("paths"))
    new_paths = _mapping(current.get("paths"))
    for path in sorted(old_paths.keys() - new_paths.keys()):
        changes.append(Change("incompatible", "route", path, "route removed"))
    for path in sorted(new_paths.keys() - old_paths.keys()):
        changes.append(Change("compatible", "route", path, "route added"))
    for path in sorted(old_paths.keys() & new_paths.keys()):
        old_path = _mapping(old_paths[path])
        new_path = _mapping(new_paths[path])
        old_methods = HTTP_METHODS & old_path.keys()
        new_methods = HTTP_METHODS & new_path.keys()
        for method in sorted(old_methods - new_methods):
            changes.append(
                Change(
                    "incompatible",
                    "method",
                    f"{method.upper()} {path}",
                    "method removed",
                )
            )
        for method in sorted(new_methods - old_methods):
            changes.append(
                Change(
                    "compatible", "method", f"{method.upper()} {path}", "method added"
                )
            )
        for method in sorted(old_methods & new_methods):
            changes.extend(
                _operation_changes(
                    _mapping(old_path[method]),
                    _mapping(new_path[method]),
                    f"{method.upper()} {path}",
                )
            )

    old_schemas = _mapping(_mapping(baseline.get("components")).get("schemas"))
    new_schemas = _mapping(_mapping(current.get("components")).get("schemas"))
    old_security = _mapping(_mapping(baseline.get("components")).get("securitySchemes"))
    new_security = _mapping(_mapping(current.get("components")).get("securitySchemes"))
    for name in sorted(old_security.keys() - new_security.keys()):
        changes.append(
            Change(
                "incompatible",
                "security",
                f"components/securitySchemes/{name}",
                "security scheme removed",
            )
        )
    for name in sorted(old_security.keys() & new_security.keys()):
        if old_security[name] != new_security[name]:
            changes.append(
                Change(
                    "incompatible",
                    "security",
                    f"components/securitySchemes/{name}",
                    "security scheme changed",
                )
            )
    for name in sorted(old_schemas.keys() - new_schemas.keys()):
        changes.append(
            Change(
                "incompatible",
                "schema",
                f"components/schemas/{name}",
                "component schema removed",
            )
        )
    for name in sorted(new_schemas.keys() - old_schemas.keys()):
        changes.append(
            Change(
                "compatible",
                "schema",
                f"components/schemas/{name}",
                "component schema added",
            )
        )
    for name in sorted(old_schemas.keys() & new_schemas.keys()):
        changes.extend(
            _schema_changes(
                _mapping(old_schemas[name]),
                _mapping(new_schemas[name]),
                f"components/schemas/{name}",
            )
        )
    return sorted(set(changes))


def _baseline_from_git(reference: str, artifact: Path) -> bytes | None:
    completed = subprocess.run(  # noqa: S603 - fixed executable and no shell
        ["/usr/bin/git", "show", f"{reference}:{artifact.as_posix()}"],
        check=False,
        capture_output=True,
    )
    if completed.returncode == 0:
        return completed.stdout
    if (
        b"does not exist" in completed.stderr
        or b"exists on disk, but not in" in completed.stderr
    ):
        return None
    raise RuntimeError(completed.stderr.decode(errors="replace").strip())


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check", "compare"))
    parser.add_argument("--artifact", type=Path, default=ARTIFACT)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--baseline-git-ref")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Generate, verify, or compare the canonical OpenAPI artifact."""
    args = _parse_args(argv)
    if args.command == "generate":
        args.artifact.parent.mkdir(parents=True, exist_ok=True)
        args.artifact.write_bytes(canonical_bytes(build_contract()))
        return 0
    if args.command == "check":
        expected = canonical_bytes(build_contract())
        actual = args.artifact.read_bytes()
        if actual != expected:
            print(
                f"{args.artifact} is stale; regenerate it with: uv run python -m scripts.openapi_contract generate",
                file=sys.stderr,
            )
            return 1
        return 0

    if bool(args.baseline) == bool(args.baseline_git_ref):
        raise ValueError("compare requires exactly one baseline source")
    baseline_content = (
        args.baseline.read_bytes()
        if args.baseline is not None
        else _baseline_from_git(args.baseline_git_ref, args.artifact)
    )
    if baseline_content is None:
        print("compatible: canonical OpenAPI artifact introduced")
        return 0
    baseline = load_document(baseline_content, source="baseline OpenAPI")
    current = load_document(args.artifact.read_bytes(), source=str(args.artifact))
    changes = classify_changes(baseline, current)
    for change in changes:
        print(
            f"{change.severity}: {change.category}: {change.location}: {change.message}"
        )
    return int(any(change.severity == "incompatible" for change in changes))


if __name__ == "__main__":
    raise SystemExit(main())
