"""Strict, bounded schemas and approved values for typed Word filling."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*")
_ROW_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TYPES = frozenset({"text", "date", "boolean", "integer"})
TYPED_DOCX_SCHEMA_VERSION = 1


class TypedTemplateError(ValueError):
    """Content-free, stable rejection with affected field paths."""

    def __init__(self, code: str, paths: tuple[str, ...] = ()) -> None:
        super().__init__(f"Typed template {code.replace('_', ' ')}")
        self.code = code
        self.paths = paths


@dataclass(frozen=True, slots=True)
class TypedDocxLimits:
    """All admission and fill limits supplied by the caller's configuration."""

    max_archive_bytes: int
    max_entries: int
    max_member_bytes: int
    max_total_bytes: int
    max_compression_ratio: float
    max_xml_elements: int
    max_xml_depth: int
    max_xml_attributes: int
    max_schema_bytes: int
    max_values_bytes: int
    max_repeat_items: int
    max_text_characters: int

    def __post_init__(self) -> None:
        integers = (
            self.max_archive_bytes,
            self.max_entries,
            self.max_member_bytes,
            self.max_total_bytes,
            self.max_xml_elements,
            self.max_xml_depth,
            self.max_xml_attributes,
            self.max_schema_bytes,
            self.max_values_bytes,
            self.max_repeat_items,
            self.max_text_characters,
        )
        if any(type(value) is not int or value <= 0 for value in integers):
            raise ValueError("Typed DOCX limits must be positive integers")
        if (
            type(self.max_compression_ratio) not in {int, float}
            or not math.isfinite(self.max_compression_ratio)
            or self.max_compression_ratio < 1
        ):
            raise ValueError(
                "Typed DOCX compression ratio must be finite and at least one"
            )


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    type: str
    required: bool
    constraints: dict[str, Any]
    has_default: bool
    default: str | int | bool | None


@dataclass(frozen=True, slots=True)
class RepeatSpec:
    name: str
    min_items: int
    max_items: int
    fields: tuple[FieldSpec, ...]


@dataclass(frozen=True, slots=True)
class TypedSchema:
    fields: tuple[FieldSpec, ...]
    repeats: tuple[RepeatSpec, ...]
    canonical_json: str
    sha256: str
    limits: TypedDocxLimits


@dataclass(frozen=True, slots=True)
class ValidatedTemplate:
    """The admitted immutable template bytes and their canonical schema."""

    schema: TypedSchema
    content: bytes
    sha256: str

    @property
    def canonical_schema_json(self) -> str:
        return self.schema.canonical_json


@dataclass(frozen=True, slots=True)
class ValidatedValues:
    """Normalized values and paths requiring human resolution."""

    normalized: dict[str, Any]
    missing_paths: tuple[str, ...]
    ambiguous_paths: tuple[str, ...]
    schema_sha256: str


def _object(value: object, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if (
        type(value) is not dict
        or not required <= value.keys()
        or value.keys() - allowed
    ):
        raise TypedTemplateError("invalid_schema")
    if any(type(key) is not str for key in value):
        raise TypedTemplateError("invalid_schema")
    return cast("dict[str, Any]", value)


def _bounded_json(value: object, maximum: int, code: str) -> str:
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise TypedTemplateError(code) from exc
    if len(encoded.encode("utf-8")) > maximum:
        raise TypedTemplateError("limit_exceeded")
    return encoded


def _date(value: object, path: str) -> str:
    if type(value) is not str or _ISO_DATE.fullmatch(value) is None:
        raise TypedTemplateError("invalid_value", (path,))
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise TypedTemplateError("invalid_value", (path,)) from exc


def _normal(
    field: FieldSpec, value: object, path: str, maximum: int
) -> str | int | bool:
    if field.type == "text":
        if (
            type(value) is not str
            or len(value) > maximum
            or any(
                unicodedata.category(character).startswith("C") for character in value
            )
        ):
            raise TypedTemplateError("invalid_value", (path,))
        minimum = field.constraints.get("min_length", 0)
        upper = field.constraints.get("max_length", maximum)
        if not minimum <= len(value) <= upper:
            raise TypedTemplateError("invalid_value", (path,))
        if "enum" in field.constraints and value not in field.constraints["enum"]:
            raise TypedTemplateError("invalid_value", (path,))
        return value
    if field.type == "date":
        normalized = _date(value, path)
        if "minimum" in field.constraints and normalized < field.constraints["minimum"]:
            raise TypedTemplateError("invalid_value", (path,))
        if "maximum" in field.constraints and normalized > field.constraints["maximum"]:
            raise TypedTemplateError("invalid_value", (path,))
        return normalized
    if field.type == "boolean":
        if type(value) is not bool:
            raise TypedTemplateError("invalid_value", (path,))
        return value
    if type(value) is not int:
        raise TypedTemplateError("invalid_value", (path,))
    if "minimum" in field.constraints and value < field.constraints["minimum"]:
        raise TypedTemplateError("invalid_value", (path,))
    if "maximum" in field.constraints and value > field.constraints["maximum"]:
        raise TypedTemplateError("invalid_value", (path,))
    return value


def _field(  # noqa: PLR0912 - each constraint form is explicitly admitted
    raw: object, limits: TypedDocxLimits, *, row: bool
) -> FieldSpec:
    data = _object(
        raw,
        {"name", "type", "required", "constraints", "default"},
        {"name", "type", "required", "constraints"},
    )
    name, kind, required = data["name"], data["type"], data["required"]
    matcher = _ROW_NAME if row else _NAME
    if (
        type(name) is not str
        or len(name) > limits.max_text_characters
        or matcher.fullmatch(name) is None
        or type(kind) is not str
        or kind not in _TYPES
        or type(required) is not bool
        or type(data["constraints"]) is not dict
    ):
        raise TypedTemplateError("invalid_schema")
    constraints = data["constraints"]
    allowed = {
        "text": {"min_length", "max_length", "enum"},
        "date": {"minimum", "maximum"},
        "integer": {"minimum", "maximum"},
        "boolean": set(),
    }[kind]
    if constraints.keys() - allowed or any(type(key) is not str for key in constraints):
        raise TypedTemplateError("invalid_schema")
    if kind == "text":
        for key in ("min_length", "max_length"):
            if key in constraints and (
                type(constraints[key]) is not int
                or constraints[key] < 0
                or constraints[key] > limits.max_text_characters
            ):
                raise TypedTemplateError("invalid_schema")
        if constraints.get("min_length", 0) > constraints.get(
            "max_length", limits.max_text_characters
        ):
            raise TypedTemplateError("invalid_schema")
        if "enum" in constraints:
            enum = constraints["enum"]
            if (
                type(enum) is not list
                or not enum
                or len(enum) > limits.max_xml_elements
                or any(
                    type(item) is not str or len(item) > limits.max_text_characters
                    for item in enum
                )
                or len(set(enum)) != len(enum)
            ):
                raise TypedTemplateError("invalid_schema")
            for item in enum:
                if (
                    not constraints.get("min_length", 0)
                    <= len(item)
                    <= constraints.get("max_length", limits.max_text_characters)
                ):
                    raise TypedTemplateError("invalid_schema")
    elif kind == "integer":
        if any(type(value) is not int for value in constraints.values()):
            raise TypedTemplateError("invalid_schema")
        if constraints.get("minimum", -math.inf) > constraints.get("maximum", math.inf):
            raise TypedTemplateError("invalid_schema")
    elif kind == "date":
        for value in constraints.values():
            try:
                _date(value, name)
            except TypedTemplateError as exc:
                raise TypedTemplateError("invalid_schema") from exc
        if constraints.get("minimum", "0000") > constraints.get("maximum", "9999"):
            raise TypedTemplateError("invalid_schema")
    spec = FieldSpec(
        name, kind, required, constraints.copy(), "default" in data, data.get("default")
    )
    if spec.has_default:
        try:
            _normal(spec, spec.default, name, limits.max_text_characters)
        except TypedTemplateError as exc:
            raise TypedTemplateError("invalid_schema") from exc
    return spec


def _unique(names: list[str]) -> bool:
    lowered = [name.casefold() for name in names]
    return len(set(lowered)) == len(lowered)


def parse_schema(raw: Mapping[str, object], limits: TypedDocxLimits) -> TypedSchema:
    """Accept only the qualified scalar and paragraph-repeat schema subset."""

    data = _object(raw, {"fields", "repeats"}, {"fields", "repeats"})
    _bounded_json(data, limits.max_schema_bytes, "invalid_schema")
    if type(data["fields"]) is not list or type(data["repeats"]) is not list:
        raise TypedTemplateError("invalid_schema")
    fields = tuple(_field(item, limits, row=False) for item in data["fields"])
    repeats: list[RepeatSpec] = []
    for raw_repeat in data["repeats"]:
        repeat = _object(
            raw_repeat,
            {"name", "min_items", "max_items", "fields"},
            {"name", "min_items", "max_items", "fields"},
        )
        name = repeat["name"]
        if (
            type(name) is not str
            or len(name) > limits.max_text_characters
            or _ROW_NAME.fullmatch(name) is None
            or type(repeat["min_items"]) is not int
            or type(repeat["max_items"]) is not int
            or not 0
            <= repeat["min_items"]
            <= repeat["max_items"]
            <= limits.max_repeat_items
            or type(repeat["fields"]) is not list
            or not repeat["fields"]
        ):
            raise TypedTemplateError("invalid_schema")
        row_fields = tuple(_field(item, limits, row=True) for item in repeat["fields"])
        if not _unique([field.name for field in row_fields]):
            raise TypedTemplateError("invalid_schema")
        repeats.append(
            RepeatSpec(name, repeat["min_items"], repeat["max_items"], row_fields)
        )
    if (
        (not fields and not repeats)
        or len(fields) + len(repeats) + sum(len(item.fields) for item in repeats)
        > limits.max_xml_elements
        or not _unique(
            [field.name for field in fields] + [item.name for item in repeats]
        )
    ):
        raise TypedTemplateError("invalid_schema")
    canonical = _bounded_json(data, limits.max_schema_bytes, "invalid_schema")
    return TypedSchema(
        fields,
        tuple(repeats),
        canonical,
        hashlib.sha256(canonical.encode()).hexdigest(),
        limits,
    )


def validate_values(  # noqa: PLR0912, PLR0915 - bounded nested values
    schema: TypedSchema | ValidatedTemplate, values: Mapping[str, object]
) -> ValidatedValues:
    """Normalize approved values while identifying questions for the human."""

    if isinstance(schema, ValidatedTemplate):
        schema = schema.schema
    if type(values) is not dict:
        raise TypedTemplateError("invalid_values")
    expected = {field.name for field in schema.fields} | {
        repeat.name for repeat in schema.repeats
    }
    if any(type(key) is not str for key in values) or values.keys() - expected:
        raise TypedTemplateError("unknown_value")
    missing: list[str] = []
    ambiguous: list[str] = []
    normalized: dict[str, Any] = {}
    for field in schema.fields:
        if field.name not in values:
            if field.has_default:
                normalized[field.name] = field.default
            elif field.required:
                missing.append(field.name)
        elif values[field.name] is None:
            ambiguous.append(field.name)
        else:
            normalized[field.name] = _normal(
                field, values[field.name], field.name, schema.limits.max_text_characters
            )
    for repeat in schema.repeats:
        if repeat.name not in values:
            if repeat.min_items:
                missing.append(repeat.name)
            else:
                normalized[repeat.name] = []
            continue
        rows = values[repeat.name]
        if rows is None:
            ambiguous.append(repeat.name)
            continue
        if (
            type(rows) is not list
            or not repeat.min_items <= len(rows) <= repeat.max_items
        ):
            raise TypedTemplateError("invalid_value", (repeat.name,))
        approved_rows: list[dict[str, Any]] = []
        row_names = {field.name for field in repeat.fields}
        for index, row in enumerate(rows):
            path = f"{repeat.name}[{index}]"
            if (
                type(row) is not dict
                or any(type(key) is not str for key in row)
                or row.keys() - row_names
            ):
                raise TypedTemplateError("invalid_value", (path,))
            row = cast("dict[str, object]", row)
            approved_row: dict[str, Any] = {}
            for field in repeat.fields:
                field_path = f"{path}.{field.name}"
                if field.name not in row:
                    if field.has_default:
                        approved_row[field.name] = field.default
                    elif field.required:
                        missing.append(field_path)
                elif row[field.name] is None:
                    ambiguous.append(field_path)
                else:
                    approved_row[field.name] = _normal(
                        field,
                        row[field.name],
                        field_path,
                        schema.limits.max_text_characters,
                    )
            approved_rows.append(approved_row)
        normalized[repeat.name] = approved_rows
    _bounded_json(values, schema.limits.max_values_bytes, "invalid_values")
    _bounded_json(normalized, schema.limits.max_values_bytes, "invalid_values")
    return ValidatedValues(normalized, tuple(missing), tuple(ambiguous), schema.sha256)
