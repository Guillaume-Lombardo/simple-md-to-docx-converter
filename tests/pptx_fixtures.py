"""Small deterministic OOXML fixtures for structured PPTX boundary tests."""

from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from xml.sax.saxutils import escape

from markweave.reversions.models import ReverseContentLimits

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = f'xmlns:a="{A}" xmlns:p="{P}" xmlns:r="{R}"'
LIMITS = ReverseContentLimits(
    1_000_000,
    2_000_000,
    100_000,
    1000,
    1000,
    1_000_000,
    1000,
    64,
    50,
    500_000,
    500_000,
    1_000_000,
    2_000_000,
)


def text_shape(
    text: str,
    *,
    paragraph_properties: str = "",
    run_properties: str = "",
    placeholder: str = "",
) -> str:
    return (
        '<p:sp><p:nvSpPr><p:cNvPr id="1" name="Text"/><p:cNvSpPr/>'
        f"<p:nvPr>{placeholder}</p:nvPr></p:nvSpPr><p:txBody>"
        f"<a:bodyPr/><a:p>{paragraph_properties}<a:r>{run_properties}"
        f"<a:t>{escape(text)}</a:t></a:r></a:p></p:txBody></p:sp>"
    )


def picture(identity: str = "image", *, description: str = "Picture") -> str:
    return (
        '<p:pic><p:nvPicPr><p:cNvPr id="2" name="Picture" '
        f'descr="{escape(description, {chr(34): "&quot;"})}"/>'
        "<p:cNvPicPr/><p:nvPr/></p:nvPicPr>"
        f'<p:blipFill><a:blip r:embed="{identity}"/></p:blipFill>'
        "<p:spPr/></p:pic>"
    )


def relations(items: tuple[tuple[str, str, str], ...]) -> bytes:
    return (
        f'<Relationships xmlns="{PKG}">'
        + "".join(
            f'<Relationship Id="{identity}" Type="{R}/{kind}" Target="{target}"/>'
            for identity, kind, target in items
        )
        + "</Relationships>"
    ).encode()


def parts(
    slides: tuple[str, ...] = ("", ""),
    *,
    notes: str | None = None,
    image: bytes | None = None,
    image_type: str = "image/png",
    order: tuple[int, ...] | None = None,
) -> dict[str, bytes]:
    selected = order if order is not None else tuple(range(1, len(slides) + 1))
    result = {
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Default Extension="png" ContentType="{image_type}"/>'
            '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
            "</Types>"
        ).encode(),
        "_rels/.rels": relations((("main", "officeDocument", "ppt/presentation.xml"),)),
        "ppt/presentation.xml": (
            f"<p:presentation {NS}><p:sldIdLst>"
            + "".join(f'<p:sldId id="{index}" r:id="s{index}"/>' for index in selected)
            + "</p:sldIdLst></p:presentation>"
        ).encode(),
        "ppt/_rels/presentation.xml.rels": relations(
            tuple(
                (f"s{index}", "slide", f"slides/s{index}.xml")
                for index in range(1, len(slides) + 1)
            )
        ),
    }
    for index, content in enumerate(slides, start=1):
        result[f"ppt/slides/s{index}.xml"] = (
            f"<p:sld {NS}><p:cSld><p:spTree>{content}</p:spTree></p:cSld></p:sld>"
        ).encode()
    slide_relationships: list[tuple[str, str, str]] = []
    if notes is not None:
        result["ppt/notesSlides/n1.xml"] = (
            f"<p:notes {NS}><p:cSld><p:spTree>{notes}</p:spTree></p:cSld></p:notes>".encode()
        )
        slide_relationships.append(("notes", "notesSlide", "../notesSlides/n1.xml"))
    if image is not None:
        result["ppt/media/image.png"] = image
        slide_relationships.append(("image", "image", "../media/image.png"))
    if slide_relationships:
        result["ppt/slides/_rels/s1.xml.rels"] = relations(tuple(slide_relationships))
    return result


def archive(members: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as package:
        for name, content in members.items():
            package.writestr(name, content)
    return output.getvalue()


def limits(**overrides: int) -> ReverseContentLimits:
    return replace(LIMITS, **overrides)
