"""Small non-executing PPTX reader preserving slide and notes boundaries."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from xml.etree import ElementTree

from markweave.reversions.assets import (
    AssetNormalizationResult,
    AssetSource,
    normalize_assets,
)
from markweave.reversions.errors import ReverseErrorCategory, reject
from markweave.reversions.models import ReverseContentLimits
from markweave.reversions.options import ReverseExtraction, ReversionOptions
from markweave.reversions.pptx_archive import (
    OFFICE_RELATIONSHIPS,
    PptxArchive,
    PptxReadLimits,
    Relationship,
)

_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_R = "{" + OFFICE_RELATIONSHIPS + "}"
_CT = "{http://schemas.openxmlformats.org/package/2006/content-types}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_ESCAPE = re.compile(r"([\\*_{}\[\]()#+.!|:>~$\-])")
_PPTX_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
)
_NOTES_DECORATIONS = frozenset({"sldImg", "sldNum", "hdr", "ftr", "dt"})
_FIRST_PRINTABLE = 32
_MAX_LIST_LEVEL = 8


def _text(value: str) -> str:
    """Render arbitrary document text as literal Markdown, never HTML or directives."""

    value = value.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if any(ord(character) < _FIRST_PRINTABLE for character in value):
        reject(ReverseErrorCategory.MALFORMED)
    value = _ESCAPE.sub(r"\\\1", value.replace("&", "&amp;").replace("<", "&lt;"))
    return value.replace(chr(96), "\\" + chr(96))


@dataclass(frozen=True, slots=True)
class PptxImage:
    """A safe image occurrence awaiting normalized path assignment."""

    occurrence: int
    description: str


@dataclass(frozen=True, slots=True)
class PptxSlide:
    """The reading order of one slide, including its separate presenter notes."""

    content: tuple[str | PptxImage, ...]
    notes: tuple[str | PptxImage, ...]
    hidden: bool


@dataclass(frozen=True, slots=True)
class StructuredPptx:
    """Bounded content and source-position assets from one presentation."""

    slides: tuple[PptxSlide, ...]
    assets: tuple[AssetSource, ...]


@dataclass(frozen=True, slots=True)
class RenderedPptx:
    markdown: str
    normalized: AssetNormalizationResult


@dataclass(slots=True)
class _Reader:
    archive: PptxArchive
    options: ReversionOptions
    limits: ReverseContentLimits
    assets: list[AssetSource] = field(default_factory=list)
    media_defaults: dict[str, str] = field(default_factory=dict)
    media_overrides: dict[str, str] = field(default_factory=dict)

    def content_types(self) -> None:
        root = self.archive.xml("[Content_Types].xml")
        if root.tag != _CT + "Types":
            reject(ReverseErrorCategory.MALFORMED)
        for entry in root:
            media = entry.get("ContentType", "")
            if entry.tag == _CT + "Default":
                identity = entry.get("Extension", "")
                mapping = self.media_defaults
            elif entry.tag == _CT + "Override":
                identity = entry.get("PartName", "").removeprefix("/")
                mapping = self.media_overrides
            else:
                reject(ReverseErrorCategory.MALFORMED)
            if not identity or not media or identity in mapping:
                reject(ReverseErrorCategory.MALFORMED)
            mapping[identity] = media

    def warning(self, kind: str) -> str:
        return (
            f"> Warning: unsupported {kind} content is represented by this placeholder."
        )

    def paragraph(  # noqa: PLR0912 - closed DrawingML paragraph variants
        self, paragraph: ElementTree.Element, relationships: dict[str, Relationship]
    ) -> str:
        pieces: list[str] = []
        for run in paragraph:
            if run.tag == _A + "br":
                pieces.append("  \n")
                continue
            if run.tag not in {_A + "r", _A + "fld"}:
                continue
            value = "".join(_text(node.text or "") for node in run.findall(_A + "t"))
            properties = run.find(_A + "rPr")
            if properties is not None:
                hyperlink = properties.find(_A + "hlinkClick")
                if hyperlink is not None:
                    identity = hyperlink.get(_R + "id", "")
                    relation = relationships.get(identity)
                    if relation is None and (identity or not hyperlink.get("action")):
                        reject(ReverseErrorCategory.MALFORMED)
                    if (
                        relation is not None
                        and relation.external
                        and relation.kind == "hyperlink"
                    ):
                        target = (
                            relation.target.replace("<", "%3C")
                            .replace(">", "%3E")
                            .replace(" ", "%20")
                        )
                        value = f"[{value}](<{target}>)"
                    else:
                        value += " (internal presentation link)"
                if value and properties.get("b") in {"1", "true"}:
                    value = f"**{value}**"
                if value and properties.get("i") in {"1", "true"}:
                    value = f"*{value}*"
            pieces.append(value)
        value = "".join(pieces)
        properties = paragraph.find(_A + "pPr")
        if properties is not None and (
            properties.find(_A + "buChar") is not None
            or properties.find(_A + "buAutoNum") is not None
        ):
            try:
                level = int(properties.get("lvl", "0"))
            except ValueError:
                reject(ReverseErrorCategory.MALFORMED)
            if not 0 <= level <= _MAX_LIST_LEVEL:
                reject(ReverseErrorCategory.MALFORMED)
            marker = "1." if properties.find(_A + "buAutoNum") is not None else "-"
            value = "  " * level + marker + " " + value
        return value

    def text_body(
        self, body: ElementTree.Element, relationships: dict[str, Relationship]
    ) -> str:
        return "\n\n".join(
            self.paragraph(paragraph, relationships)
            for paragraph in body.findall(_A + "p")
        )

    def table(
        self, table: ElementTree.Element, relationships: dict[str, Relationship]
    ) -> str:
        rows: list[list[str]] = []
        merged = False
        for row in table.findall(_A + "tr"):
            cells: list[str] = []
            for cell in row.findall(_A + "tc"):
                merged |= any(
                    cell.get(name, "1") != "1" for name in ("gridSpan", "rowSpan")
                )
                merged |= any(
                    cell.get(name, "0") in {"1", "true"}
                    for name in ("hMerge", "vMerge")
                )
                body = cell.find(_A + "txBody")
                cells.append(
                    self.text_body(body, relationships).replace("\n", " ")
                    if body is not None
                    else ""
                )
            rows.append(cells)
        width = max((len(row) for row in rows), default=0)
        if not width:
            return self.warning("table")
        # Ragged input must not amplify into rows*width allocations before the
        # output ceiling is checked. Count separators, padding and literal UTF-8.
        projected = (
            3 * width * (len(rows) + 3)
            + 2 * len(rows)
            + 3
            + sum(len(cell.encode("utf-8")) for row in rows for cell in row)
        )
        if projected > self.limits.max_markdown_bytes:
            reject(ReverseErrorCategory.RESOURCE_LIMIT)
        lines = [
            "| " + " | ".join([""] * width) + " |",
            "| " + " | ".join(["---"] * width) + " |",
        ]
        lines.extend(
            "| " + " | ".join(row + [""] * (width - len(row))) + " |" for row in rows
        )
        if merged:
            lines.append(
                "\n> Warning: merged table cells were flattened; text is retained."
            )
        return "\n".join(lines)

    def picture(
        self, shape: ElementTree.Element, relationships: dict[str, Relationship]
    ) -> PptxImage | str:
        if not self.options.include_images:
            return "[Image omitted by request.]"
        blip = shape.find(".//" + _A + "blip")
        if blip is None:
            return self.warning("image")
        if blip.get(_R + "link") is not None:
            reject(ReverseErrorCategory.ASSET_INVALID)
        relation = relationships.get(blip.get(_R + "embed", ""))
        if relation is None or relation.kind != "image" or relation.external:
            reject(ReverseErrorCategory.MALFORMED)
        media = self.media_overrides.get(relation.target) or self.media_defaults.get(
            relation.target.rpartition(".")[2]
        )
        data = self.archive.read(relation.target)
        if len(data) > self.limits.max_image_source_bytes:
            reject(ReverseErrorCategory.RESOURCE_LIMIT)
        supported = media in {
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
            "image/svg+xml",
        }
        index = len(self.assets)
        if index >= self.limits.max_asset_count:
            reject(ReverseErrorCategory.RESOURCE_LIMIT)
        self.assets.append(
            AssetSource(
                relation.target,
                data if supported else None,
                media if supported else None,
            )
        )
        properties = shape.find(".//" + _P + "cNvPr")
        description = properties.get("descr", "") if properties is not None else ""
        return PptxImage(index, _text(description))

    def shapes(  # noqa: PLR0912 - closed presentation shape variants
        self,
        tree: ElementTree.Element,
        relationships: dict[str, Relationship],
        *,
        notes: bool = False,
    ) -> tuple[str | PptxImage, ...]:
        output: list[str | PptxImage] = []
        for shape in tree:
            if shape.tag in {_P + "nvGrpSpPr", _P + "grpSpPr", _P + "extLst"}:
                continue
            if shape.tag == _P + "grpSp":
                output.extend(self.shapes(shape, relationships, notes=notes))
            elif shape.tag in {_P + "sp", _P + "cxnSp"}:
                placeholder = shape.find(".//" + _P + "ph")
                if (
                    notes
                    and placeholder is not None
                    and placeholder.get("type") in _NOTES_DECORATIONS
                ):
                    continue
                body = shape.find(_P + "txBody")
                if body is not None:
                    text = self.text_body(body, relationships)
                    if text.strip():
                        output.append(text)
                    elif placeholder is None:
                        output.append(self.warning("drawing"))
                else:
                    output.append(self.warning("drawing"))
                if shape.find(_P + "spPr/" + _A + "blipFill") is not None:
                    output.append(self.picture(shape, relationships))
            elif shape.tag == _P + "pic":
                output.append(self.picture(shape, relationships))
            elif shape.tag == _P + "graphicFrame":
                table = shape.find(".//" + _A + "tbl")
                output.append(
                    self.table(table, relationships)
                    if table is not None
                    else self.warning("chart, diagram or embedded object")
                )
            elif shape.tag == _MC + "AlternateContent":
                fallback = shape.find(_MC + "Fallback")
                output.append(self.warning("alternate drawing"))
                if fallback is not None:
                    output.extend(self.shapes(fallback, relationships, notes=notes))
            else:
                output.append(self.warning("presentation object"))
            if shape.tag != _P + "grpSp" and any(
                node.tag.rpartition("}")[2] in {"videoFile", "audioFile", "media"}
                for node in shape.iter()
            ):
                output.append(self.warning("audio or video"))
        return tuple(output)

    def inherited_warnings(
        self, relationships: dict[str, Relationship]
    ) -> tuple[str, ...]:
        """Expose inherited objects that this content reader cannot place faithfully."""

        warnings: list[str] = []
        for kind, tag in (("slideLayout", "sldLayout"), ("slideMaster", "sldMaster")):
            candidates = [item for item in relationships.values() if item.kind == kind]
            if len(candidates) > 1:
                reject(ReverseErrorCategory.MALFORMED)
            if not candidates:
                break
            part = candidates[0].target
            root = self.archive.xml(part)
            if root.tag != _P + tag:
                reject(ReverseErrorCategory.MALFORMED)
            if any(
                (node.tag == _A + "t" and bool(node.text and node.text.strip()))
                or node.tag
                in {_A + "blip", _P + "pic", _P + "graphicFrame", _P + "cxnSp"}
                or (
                    node.tag == _P + "sp"
                    and node.find(".//" + _P + "ph") is None
                    and node.find(_P + "spPr") is not None
                )
                for node in root.iter()
            ):
                warnings.append(self.warning("inherited layout or master"))
            relationships = self.archive.relationships(part)
        return tuple(warnings)

    def slide(self, part: str) -> PptxSlide:
        root = self.archive.xml(part)
        if root.tag != _P + "sld":
            reject(ReverseErrorCategory.MALFORMED)
        relationships = self.archive.relationships(part)
        tree = root.find(_P + "cSld/" + _P + "spTree")
        if tree is None:
            reject(ReverseErrorCategory.MALFORMED)
        content = list(self.shapes(tree, relationships))
        content.extend(self.inherited_warnings(relationships))
        background = root.find(_P + "cSld/" + _P + "bg")
        if background is not None and background.find(".//" + _A + "blip") is not None:
            content.append(self.warning("background image"))
        notes: tuple[str | PptxImage, ...] = ()
        notes_relations = [
            item for item in relationships.values() if item.kind == "notesSlide"
        ]
        if len(notes_relations) > 1:
            reject(ReverseErrorCategory.MALFORMED)
        if notes_relations and self.options.include_notes:
            notes_part = notes_relations[0].target
            notes_root = self.archive.xml(notes_part)
            notes_tree = notes_root.find(_P + "cSld/" + _P + "spTree")
            if notes_root.tag != _P + "notes" or notes_tree is None:
                reject(ReverseErrorCategory.MALFORMED)
            notes = self.shapes(
                notes_tree, self.archive.relationships(notes_part), notes=True
            )
        return PptxSlide(tuple(content), notes, root.get("show") in {"0", "false"})

    def read(self) -> StructuredPptx:
        self.content_types()
        main = [
            item
            for item in self.archive.relationships("").values()
            if item.kind == "officeDocument"
        ]
        if len(main) != 1 or self.media_overrides.get(main[0].target) != _PPTX_TYPE:
            reject(ReverseErrorCategory.MALFORMED)
        part = main[0].target
        root = self.archive.xml(part)
        if root.tag != _P + "presentation":
            reject(ReverseErrorCategory.MALFORMED)
        relationships = self.archive.relationships(part)
        listing = root.find(_P + "sldIdLst")
        if listing is None or not len(listing):
            reject(ReverseErrorCategory.MALFORMED)
        parts: list[str] = []
        identities: set[str] = set()
        for item in listing:
            identity = item.get("id", "")
            relation = relationships.get(item.get(_R + "id", ""))
            if (
                item.tag != _P + "sldId"
                or not identity
                or identity in identities
                or relation is None
                or relation.kind != "slide"
                or relation.target in parts
            ):
                reject(ReverseErrorCategory.MALFORMED)
            identities.add(identity)
            parts.append(relation.target)
        slides = tuple(self.slide(part) for part in parts)
        return StructuredPptx(slides, tuple(self.assets))


def read_pptx(
    source: bytes, options: ReversionOptions, limits: ReverseContentLimits
) -> StructuredPptx:
    """Read slides in presentation relationship order inside the isolated attempt."""

    archive = PptxArchive(source, PptxReadLimits.from_content_limits(limits))
    try:
        return _Reader(archive, options, limits).read()
    finally:
        archive.close()


def _render_content(
    content: tuple[str | PptxImage, ...], paths: tuple[PurePosixPath | None, ...]
) -> str:
    result: list[str] = []
    for item in content:
        if isinstance(item, str):
            if item:
                result.append(item)
        else:
            path = paths[item.occurrence]
            if path is None:
                result.append(
                    "> Warning: unsupported image content is unavailable at this position."
                )
            else:
                result.append(f"![{item.description}]({path.as_posix()})")
    return "\n\n".join(result)


def render_pptx(
    source: bytes, options: ReversionOptions, limits: ReverseContentLimits
) -> RenderedPptx:
    """Normalize embedded pictures and render deterministic literal slide Markdown."""

    parsed = read_pptx(source, options, limits)
    normalized = normalize_assets(parsed.assets, limits.asset_limits)
    paths = tuple(reference.path for reference in normalized.references)
    slides: list[str] = []
    for ordinal, slide in enumerate(parsed.slides, start=1):
        content = _render_content(slide.content, paths)
        if not content:
            content = f"<!-- Empty slide {ordinal}. -->"
        if slide.hidden:
            content = (
                "> Warning: this slide is hidden in the source presentation.\n\n"
                + content
            )
        if slide.notes:
            notes = _render_content(slide.notes, paths)
            if options.extraction is ReverseExtraction.MARP:
                content += "\n\n<!--\n" + notes + "\n-->"
            else:
                content += "\n\n::: notes\n" + notes + "\n:::"
        slides.append(content)
    markdown = "\n\n---\n\n".join(slides) + "\n"
    if options.extraction is ReverseExtraction.MARP:
        markdown = "---\nmarp: true\n---\n\n" + markdown
    if len(markdown.encode("utf-8")) > limits.max_markdown_bytes:
        reject(ReverseErrorCategory.RESOURCE_LIMIT)
    return RenderedPptx(markdown, normalized)
