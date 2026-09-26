"""Strict typed schema and value admission without a document engine."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import asdict

import pytest

from markweave.composer.typed_templates import (
    TYPED_DOCX_SCHEMA_VERSION,
    TypedDocxLimits,
    TypedTemplateError,
    parse_schema,
    validate_values,
)


@pytest.fixture
def limits() -> TypedDocxLimits:
    return TypedDocxLimits(
        max_archive_bytes=100_000,
        max_entries=30,
        max_member_bytes=100_000,
        max_total_bytes=200_000,
        max_compression_ratio=100,
        max_xml_elements=1_000,
        max_xml_depth=50,
        max_xml_attributes=2_000,
        max_schema_bytes=4_000,
        max_values_bytes=4_000,
        max_repeat_items=3,
        max_text_characters=80,
    )


@pytest.fixture
def schema() -> dict[str, object]:
    return {
        "fields": [
            {
                "name": "author",
                "type": "text",
                "required": True,
                "constraints": {"min_length": 1, "max_length": 40},
            },
            {
                "name": "due",
                "type": "date",
                "required": False,
                "constraints": {"minimum": "2026-01-01", "maximum": "2026-12-31"},
                "default": "2026-09-24",
            },
            {
                "name": "approved",
                "type": "boolean",
                "required": False,
                "constraints": {},
            },
        ],
        "repeats": [
            {
                "name": "findings",
                "min_items": 1,
                "max_items": 2,
                "fields": [
                    {
                        "name": "title",
                        "type": "text",
                        "required": True,
                        "constraints": {"enum": ["A", "B"]},
                    },
                    {
                        "name": "score",
                        "type": "integer",
                        "required": False,
                        "constraints": {"minimum": 0, "maximum": 10},
                        "default": 0,
                    },
                ],
            }
        ],
    }


@pytest.mark.unit
def test_defaults_missing_ambiguity_and_canonical_schema(
    schema: dict[str, object], limits: TypedDocxLimits
) -> None:
    parsed = parse_schema(schema, limits)
    assert TYPED_DOCX_SCHEMA_VERSION == 1
    assert parsed.canonical_json.startswith('{"fields":')
    result = validate_values(parsed, {"author": None, "findings": [{"title": "A"}]})
    assert result.missing_paths == ()
    assert result.ambiguous_paths == ("author",)
    assert result.normalized == {
        "due": "2026-09-24",
        "findings": [{"title": "A", "score": 0}],
    }
    complete = validate_values(parsed, {"author": "Ada", "findings": [{"title": "B"}]})
    assert complete.normalized["author"] == "Ada"
    assert "approved" not in complete.normalized
    assert not complete.missing_paths and not complete.ambiguous_paths


@pytest.mark.unit
def test_missing_nested_required_path(
    schema: dict[str, object], limits: TypedDocxLimits
) -> None:
    result = validate_values(parse_schema(schema, limits), {"findings": [{}]})
    assert result.missing_paths == ("author", "findings[0].title")


@pytest.mark.unit
@pytest.mark.parametrize(
    "change",
    [
        lambda data: data.update(extra=True),
        lambda data: data["fields"].append(copy.deepcopy(data["fields"][0])),
        lambda data: data["fields"][0].update(type="unsupported"),
        lambda data: data["fields"][0].update(default=""),
        lambda data: data["fields"][0]["constraints"].update(pattern=".*"),
        lambda data: data["repeats"][0].update(max_items=4),
        lambda data: data["repeats"][0]["fields"].append(
            copy.deepcopy(data["repeats"][0]["fields"][0])
        ),
    ],
)
def test_malformed_schema_rejected(
    schema: dict[str, object],
    limits: TypedDocxLimits,
    change: Callable[[dict[str, object]], object],
) -> None:
    change(schema)
    with pytest.raises(TypedTemplateError) as rejected:
        parse_schema(schema, limits)
    assert rejected.value.code == "invalid_schema"


@pytest.mark.unit
@pytest.mark.parametrize(
    "values,path",
    [
        ({"author": "", "findings": [{"title": "A"}]}, "author"),
        ({"author": "Ada", "due": "2026-02-31", "findings": [{"title": "A"}]}, "due"),
        ({"author": "Ada", "approved": 1, "findings": [{"title": "A"}]}, "approved"),
        ({"author": "Ada", "findings": [{"title": "C"}]}, "findings[0].title"),
        (
            {"author": "Ada", "findings": [{"title": "A", "score": True}]},
            "findings[0].score",
        ),
        ({"author": "Ada", "findings": []}, "findings"),
    ],
)
def test_invalid_values_rejected(
    schema: dict[str, object],
    limits: TypedDocxLimits,
    values: dict[str, object],
    path: str,
) -> None:
    with pytest.raises(TypedTemplateError) as rejected:
        validate_values(parse_schema(schema, limits), values)
    assert rejected.value.code == "invalid_value"
    assert rejected.value.paths == (path,)


@pytest.mark.unit
def test_unknown_value_rejected(
    schema: dict[str, object], limits: TypedDocxLimits
) -> None:
    with pytest.raises(TypedTemplateError) as rejected:
        validate_values(
            parse_schema(schema, limits),
            {"author": "Ada", "findings": [{}], "other": "x"},
        )
    assert rejected.value.code == "unknown_value"


@pytest.mark.unit
@pytest.mark.parametrize(
    "change",
    [
        {"max_entries": 0},
        {"max_compression_ratio": 0},
        {"max_compression_ratio": float("nan")},
    ],
)
def test_invalid_caller_limits_rejected(
    limits: TypedDocxLimits, change: dict[str, object]
) -> None:
    configuration = asdict(limits)
    configuration.update(change)
    with pytest.raises(ValueError):
        TypedDocxLimits(**configuration)


@pytest.mark.unit
def test_bounded_schema_and_value_json(
    schema: dict[str, object], limits: TypedDocxLimits
) -> None:
    configuration = asdict(limits)
    configuration["max_schema_bytes"] = 10
    with pytest.raises(TypedTemplateError) as rejected:
        parse_schema(schema, TypedDocxLimits(**configuration))
    assert rejected.value.code == "limit_exceeded"
    configuration = asdict(limits)
    configuration["max_values_bytes"] = 10
    parsed = parse_schema(schema, TypedDocxLimits(**configuration))
    with pytest.raises(TypedTemplateError) as rejected:
        validate_values(parsed, {"author": "Ada", "findings": [{"title": "A"}]})
    assert rejected.value.code == "limit_exceeded"


@pytest.mark.unit
@pytest.mark.parametrize(
    "change",
    [
        lambda data: data["fields"][0]["constraints"].update(min_length=-1),
        lambda data: data["fields"][0]["constraints"].update(min_length=41),
        lambda data: data["fields"][0]["constraints"].update(enum=["A", "A"]),
        lambda data: data["fields"][0]["constraints"].update(enum=[""]),
        lambda data: data["fields"][1]["constraints"].update(maximum="2025-01-01"),
        lambda data: data["fields"][1]["constraints"].update(minimum="2026-02-31"),
        lambda data: data["repeats"][0]["fields"][1]["constraints"].update(
            minimum=True
        ),
        lambda data: data["repeats"][0]["fields"][1]["constraints"].update(minimum=11),
        lambda data: data["repeats"][0]["fields"][0].update(name="bad.name"),
        lambda data: data["repeats"][0].update(fields=[]),
        lambda data: data.update(fields="wrong"),
        lambda data: data.update(fields=[], repeats=[]),
    ],
)
def test_constraint_contract_rejected(
    schema: dict[str, object],
    limits: TypedDocxLimits,
    change: Callable[[dict[str, object]], object],
) -> None:
    change(schema)
    with pytest.raises(TypedTemplateError) as rejected:
        parse_schema(schema, limits)
    assert rejected.value.code == "invalid_schema"


@pytest.mark.unit
@pytest.mark.parametrize(
    "values,path",
    [
        ({"author": "Ada", "due": "2025-12-31", "findings": [{"title": "A"}]}, "due"),
        ({"author": "Ada", "due": "2027-01-01", "findings": [{"title": "A"}]}, "due"),
        ({"author": "Ada\n", "findings": [{"title": "A"}]}, "author"),
        ({"author": 4, "findings": [{"title": "A"}]}, "author"),
        ({"author": "X" * 41, "findings": [{"title": "A"}]}, "author"),
        (
            {"author": "Ada", "findings": [{"title": "A", "score": 11}]},
            "findings[0].score",
        ),
        (
            {"author": "Ada", "findings": [{"title": "A", "score": -1}]},
            "findings[0].score",
        ),
        ({"author": "Ada", "findings": [{"title": "A", "extra": "x"}]}, "findings[0]"),
    ],
)
def test_more_invalid_value_paths(
    schema: dict[str, object],
    limits: TypedDocxLimits,
    values: dict[str, object],
    path: str,
) -> None:
    with pytest.raises(TypedTemplateError) as rejected:
        validate_values(parse_schema(schema, limits), values)
    assert rejected.value.code == "invalid_value"
    assert rejected.value.paths == (path,)


@pytest.mark.unit
def test_repeat_ambiguity_is_reported(
    schema: dict[str, object], limits: TypedDocxLimits
) -> None:
    parsed = parse_schema(schema, limits)
    result = validate_values(parsed, {"author": "Ada", "findings": None})
    assert result.ambiguous_paths == ("findings",)
    result = validate_values(parsed, {"author": "Ada", "findings": [{"title": None}]})
    assert result.ambiguous_paths == ("findings[0].title",)
    result = validate_values(parsed, {"author": "Ada"})
    assert result.missing_paths == ("findings",)
