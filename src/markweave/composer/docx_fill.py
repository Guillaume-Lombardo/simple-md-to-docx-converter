"""Deterministic, narrow OOXML content-control filling for admitted DOCX files."""

from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile
from collections.abc import Mapping
from xml.etree import ElementTree

from markweave.composer.typed_templates import (
    FieldSpec,
    TypedDocxLimits,
    TypedSchema,
    TypedTemplateError,
    ValidatedTemplate,
    ValidatedValues,
    parse_schema,
    validate_values,
)
from markweave.templates import validation as package_validation
from markweave.templates.errors import TemplateValidationError

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_W_NS}}}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_MAIN = "word/document.xml"
_REQUIRED = frozenset({"[Content_Types].xml", "_rels/.rels", _MAIN})
_SDT_CHILD_COUNT = 2
_UNSAFE_PART_MARKERS = ("_xmlsignatures", "digitalsignature", ".sigs", "vba", "macros")
_UNSAFE_REL_MARKERS = ("digital-signature", "digitalsignature", "vba", "macro")
TYPED_DOCX_FILL_ENGINE_VERSION = 1
ElementTree.register_namespace("w", _W_NS)


def _package_limits(limits: TypedDocxLimits) -> package_validation.TemplateLimits:
    return package_validation.TemplateLimits(
        limits.max_archive_bytes,
        limits.max_entries,
        limits.max_member_bytes,
        limits.max_total_bytes,
        limits.max_compression_ratio,
        limits.max_xml_elements,
        limits.max_xml_depth,
        limits.max_xml_attributes,
        limits.max_entries,
        limits.max_text_characters,
    )


def _admit_package(  # noqa: PLR0912 - fail-closed OPC validation
    content: bytes, limits: TypedDocxLimits
) -> tuple[dict[str, bytes], dict[str, ElementTree.Element]]:
    """Reuse the existing Word archive preflight, bounded reads and OPC checks."""

    if type(content) is not bytes or not content:
        raise TypedTemplateError("invalid_package")
    if len(content) > limits.max_archive_bytes:
        raise TypedTemplateError("limit_exceeded")
    configured = _package_limits(limits)
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        with archive:
            members = package_validation._preflight(archive, configured)
            payloads: dict[str, bytes] = {}
            total = 0
            for member in members:
                if member.info.is_dir():
                    continue
                payloads[member.name], total = package_validation._read_member(
                    archive, member, configured, total
                )
        if not payloads.keys() >= _REQUIRED:
            raise TypedTemplateError("invalid_package")
        if any(
            any(marker in name.casefold() for marker in _UNSAFE_PART_MARKERS)
            for name in payloads
        ):
            raise TypedTemplateError("active_content")
        package_validation._inspect_active_parts(payloads)
        roots = {
            name: package_validation._parse_xml(payload, configured)
            for name, payload in payloads.items()
            if name == "[Content_Types].xml" or name.endswith((".xml", ".rels"))
        }
        if (
            roots["[Content_Types].xml"].tag
            != "{http://schemas.openxmlformats.org/package/2006/content-types}Types"
        ):
            raise TypedTemplateError("invalid_package")
        if roots[_MAIN].tag != _W + "document":
            raise TypedTemplateError("invalid_package")
        if any(
            node.tag.startswith(_MC)
            or any(attribute.startswith(_MC) for attribute in node.attrib)
            for node in roots[_MAIN].iter()
        ):
            raise TypedTemplateError("unsupported_target")
        package_validation._inspect_content_types(roots["[Content_Types].xml"])
        identifiers = package_validation._inspect_relationships(
            roots, {package_validation._part_key(name) for name in payloads}
        )
        package_validation._inspect_relationship_references(roots, identifiers)
        for name, root in roots.items():
            if name != _MAIN and any(node.tag == _W + "sdt" for node in root.iter()):
                raise TypedTemplateError("unsupported_target")
            if name.endswith(".rels"):
                for node in root.iter():
                    if any(
                        marker in node.get("Type", "").casefold()
                        for marker in _UNSAFE_REL_MARKERS
                    ):
                        raise TypedTemplateError("active_content")
        return payloads, roots
    except TypedTemplateError:
        raise
    except TemplateValidationError as exc:
        raise TypedTemplateError(exc.code.value) from exc
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise TypedTemplateError("invalid_package") from exc


def _tag(control: ElementTree.Element) -> str:
    if (
        control.tag != _W + "sdt"
        or len(control.findall(_W + "sdtPr")) != 1
        or len(control.findall(_W + "sdtContent")) != 1
        or len(control) != _SDT_CHILD_COUNT
    ):
        raise TypedTemplateError("unsupported_target")
    properties = control.find(_W + "sdtPr")
    if properties is None:
        raise TypedTemplateError("unsupported_target")
    tags = properties.findall(_W + "tag")
    if len(tags) != 1 or len(tags[0].attrib) != 1:
        raise TypedTemplateError("unsupported_target")
    value = tags[0].get(_W + "val")
    if not value:
        raise TypedTemplateError("unsupported_target")
    allowed = {_W + "tag", _W + "id", _W + "alias", _W + "text"}
    if any(child.tag not in allowed for child in properties):
        raise TypedTemplateError("unsupported_target", (value,))
    text_controls = properties.findall(_W + "text")
    if len(text_controls) > 1 or any(item.attrib for item in text_controls):
        raise TypedTemplateError("unsupported_target", (value,))
    return value


def _plain_run(
    run: ElementTree.Element, *, editable: bool
) -> ElementTree.Element | None:
    if run.tag != _W + "r" or any(
        child.tag not in {_W + "rPr", _W + "t"} for child in run
    ):
        raise TypedTemplateError("unsupported_target")
    texts = run.findall(_W + "t")
    if len(texts) != 1 or len(run.findall(_W + "rPr")) > 1:
        raise TypedTemplateError("unsupported_target")
    if editable and run.attrib:
        raise TypedTemplateError("unsupported_target")
    return texts[0]


def _scalar_text(control: ElementTree.Element) -> ElementTree.Element:
    content = control.find(_W + "sdtContent")
    if content is None or len(content) != 1:
        raise TypedTemplateError("unsupported_target", (_tag(control),))
    item = content[0]
    if item.tag == _W + "p":
        children = [child for child in item if child.tag != _W + "pPr"]
        if len(children) != 1 or len(item.findall(_W + "pPr")) > 1:
            raise TypedTemplateError("unsupported_target", (_tag(control),))
        item = children[0]
    elif item.tag != _W + "r":
        raise TypedTemplateError("unsupported_target", (_tag(control),))
    text = _plain_run(item, editable=True)
    if text is None:
        raise TypedTemplateError("unsupported_target", (_tag(control),))
    return text


def _repeat_prototype(
    control: ElementTree.Element, row_fields: tuple[FieldSpec, ...]
) -> ElementTree.Element:
    content = control.find(_W + "sdtContent")
    if content is None or len(content) != 1 or content[0].tag != _W + "p":
        raise TypedTemplateError("unsupported_target", (_tag(control),))
    paragraph = content[0]
    if paragraph.attrib or len(paragraph.findall(_W + "pPr")) > 1:
        raise TypedTemplateError("unsupported_target", (_tag(control),))
    names: list[str] = []
    for child in paragraph:
        if child.tag == _W + "pPr":
            continue
        if child.tag == _W + "r":
            _plain_run(child, editable=False)
            continue
        if child.tag != _W + "sdt":
            raise TypedTemplateError("unsupported_target", (_tag(control),))
        tag = _tag(child)
        properties = child.find(_W + "sdtPr")
        if properties is None or properties.find(_W + "id") is not None:
            raise TypedTemplateError("unsupported_target", (tag,))
        _scalar_text(child)
        names.append(tag)
    expected = {
        _tag(control).removeprefix("repeat:") + "." + field.name for field in row_fields
    }
    if len(names) != len(expected) or set(names) != expected:
        raise TypedTemplateError("unsupported_target", (_tag(control),))
    return paragraph


def _controls(
    root: ElementTree.Element, schema: TypedSchema
) -> tuple[dict[str, ElementTree.Element], dict[str, ElementTree.Element]]:
    body = root.find(_W + "body")
    if body is None:
        raise TypedTemplateError("invalid_package")
    schema_fields = schema.fields
    schema_repeats = schema.repeats
    expected_fields = {field.name for field in schema_fields}
    expected_repeats = {repeat.name for repeat in schema_repeats}
    field_controls: dict[str, ElementTree.Element] = {}
    repeat_controls: dict[str, ElementTree.Element] = {}
    parents = {child: parent for parent in root.iter() for child in parent}
    for control in root.iter(_W + "sdt"):
        tag = _tag(control)
        ancestors: list[ElementTree.Element] = []
        parent = parents.get(control)
        while parent is not None:
            ancestors.append(parent)
            parent = parents.get(parent)
        nested_repeat = next(
            (ancestor for ancestor in ancestors if ancestor.tag == _W + "sdt"), None
        )
        if nested_repeat is not None:
            if (
                tag
                not in {
                    f"{repeat.name}.{field.name}"
                    for repeat in schema_repeats
                    for field in repeat.fields
                }
                or _tag(nested_repeat) != f"repeat:{tag.split('.', 1)[0]}"
            ):
                raise TypedTemplateError("unsupported_target", (tag,))
            continue
        if tag.startswith("repeat:"):
            name = tag.removeprefix("repeat:")
            if (
                name not in expected_repeats
                or name in repeat_controls
                or parents.get(control) is not body
            ):
                raise TypedTemplateError("unsupported_target", (tag,))
            repeat_controls[name] = control
        else:
            if tag not in expected_fields or tag in field_controls:
                raise TypedTemplateError("unsupported_target", (tag,))
            if parents.get(control) is not body and not (
                parents.get(control) is not None
                and parents[control].tag == _W + "p"
                and parents.get(parents[control]) is body
            ):
                raise TypedTemplateError("unsupported_target", (tag,))
            _scalar_text(control)
            field_controls[tag] = control
    if (
        set(field_controls) != expected_fields
        or set(repeat_controls) != expected_repeats
    ):
        raise TypedTemplateError(
            "missing_target",
            tuple(
                sorted(
                    (expected_fields - field_controls.keys())
                    | (expected_repeats - repeat_controls.keys())
                )
            ),
        )
    for repeat in schema_repeats:
        _repeat_prototype(repeat_controls[repeat.name], repeat.fields)
    return field_controls, repeat_controls


def validate_template(
    schema: Mapping[str, object], docx: bytes, limits: TypedDocxLimits
) -> ValidatedTemplate:
    """Admit a schema and a matching, bounded untrusted DOCX content-control package."""

    parsed = parse_schema(schema, limits)
    _, roots = _admit_package(docx, limits)
    _controls(roots[_MAIN], parsed)
    return ValidatedTemplate(parsed, docx, hashlib.sha256(docx).hexdigest())


def _display(value: object) -> str:
    if value is None:
        return ""
    if type(value) is bool:
        return "true" if value else "false"
    return str(value)


def _replace(control: ElementTree.Element, value: object) -> None:
    text = _scalar_text(control)
    text.text = _display(value)
    if text.text.startswith(" ") or text.text.endswith(" "):
        text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _write_package(source: bytes, document_xml: bytes) -> bytes:
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(source)) as original,
        zipfile.ZipFile(output, "w") as target,
    ):
        for info in original.infolist():
            payload = document_xml if info.filename == _MAIN else original.read(info)
            target.writestr(info, payload)
    return output.getvalue()


def fill_docx(
    template: ValidatedTemplate, values: ValidatedValues, limits: TypedDocxLimits
) -> bytes:
    """Return a new DOCX with only qualified content controls filled."""

    if (
        template.schema.limits != limits
        or hashlib.sha256(template.content).hexdigest() != template.sha256
    ):
        raise TypedTemplateError("template_changed")
    if (
        values.schema_sha256 != template.schema.sha256
        or values.missing_paths
        or values.ambiguous_paths
    ):
        raise TypedTemplateError(
            "unresolved_values", values.missing_paths + values.ambiguous_paths
        )
    parsed = parse_schema(json.loads(template.canonical_schema_json), limits)
    approved = validate_values(parsed, values.normalized)
    if approved.missing_paths or approved.ambiguous_paths:
        raise TypedTemplateError(
            "unresolved_values", approved.missing_paths + approved.ambiguous_paths
        )
    payloads, roots = _admit_package(template.content, limits)
    root = roots[_MAIN]
    fields, repeats = _controls(root, parsed)
    for name, control in fields.items():
        _replace(control, approved.normalized.get(name))
    for repeat in parsed.repeats:
        control = repeats[repeat.name]
        content = control.find(_W + "sdtContent")
        if content is None:
            raise TypedTemplateError("unsupported_target", (repeat.name,))
        prototype = content[0]
        content.remove(prototype)
        for row in approved.normalized.get(repeat.name, []):
            clone = copy.deepcopy(prototype)
            for nested in clone.iter(_W + "sdt"):
                tag = _tag(nested)
                field_name = tag.removeprefix(repeat.name + ".")
                _replace(nested, row.get(field_name))
            content.append(clone)
    document_xml = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    if len(document_xml) > limits.max_member_bytes:
        raise TypedTemplateError("limit_exceeded")
    output = _write_package(template.content, document_xml)
    _admit_package(output, limits)
    with zipfile.ZipFile(io.BytesIO(output)) as archive:
        for name, original in payloads.items():
            if name != _MAIN and archive.read(name) != original:
                raise TypedTemplateError("package_changed")
    return output
