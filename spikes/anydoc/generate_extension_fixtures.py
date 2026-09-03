"""Generate deterministic fixtures for admitted extension aliases."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)

_RELATIONSHIPS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{target}"/>
</Relationships>
"""

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/{part}" ContentType="{content_type}"/>
{extra_overrides}</Types>
"""

_WORD_DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Generated DOCM fixture</w:t></w:r></w:p><w:sectPr/></w:body>
</w:document>
"""

_PRESENTATION = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:sldIdLst><p:sldId id="256" r:id="rId1"/></p:sldIdLst>
  <p:sldSz cx="9144000" cy="6858000"/><p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>
"""

_PRESENTATION_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
</Relationships>
"""

_SLIDE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
  <p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
  <p:grpSpPr><a:xfrm/></p:grpSpPr></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>
"""

_WORKBOOK = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>
"""

_WORKBOOK_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""

_WORKSHEET = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>Generated XLSM fixture</t></is></c></row></sheetData>
</worksheet>
"""


def _write_zip(path: Path, entries: tuple[tuple[str, str], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, content in entries:
            info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)


def generate(corpus: Path) -> None:
    """Generate every alias fixture below ``corpus``."""
    binary_source = corpus / "ppt" / "handmade-multimaster.ppt"
    for extension in ("pps", "pot"):
        destination = corpus / extension / f"handmade-multimaster.{extension}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(binary_source, destination)

    _write_zip(
        corpus / "docm" / "generated.docm",
        (
            (
                "[Content_Types].xml",
                _CONTENT_TYPES.format(
                    part="word/document.xml",
                    content_type="application/vnd.ms-word.document.macroEnabled.main+xml",
                    extra_overrides="",
                ),
            ),
            ("_rels/.rels", _RELATIONSHIPS.format(target="word/document.xml")),
            ("word/document.xml", _WORD_DOCUMENT),
        ),
    )

    presentation_types = {
        "pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
        "ppsx": "application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml",
        "ppsm": "application/vnd.ms-powerpoint.slideshow.macroEnabled.main+xml",
    }
    slide_override = '  <Override PartName="/ppt/slides/slide1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>\n'
    for extension, content_type in presentation_types.items():
        _write_zip(
            corpus / extension / f"generated.{extension}",
            (
                (
                    "[Content_Types].xml",
                    _CONTENT_TYPES.format(
                        part="ppt/presentation.xml",
                        content_type=content_type,
                        extra_overrides=slide_override,
                    ),
                ),
                ("_rels/.rels", _RELATIONSHIPS.format(target="ppt/presentation.xml")),
                ("ppt/presentation.xml", _PRESENTATION),
                ("ppt/_rels/presentation.xml.rels", _PRESENTATION_RELS),
                ("ppt/slides/slide1.xml", _SLIDE),
            ),
        )

    worksheet_override = '  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>\n'
    _write_zip(
        corpus / "xlsm" / "generated.xlsm",
        (
            (
                "[Content_Types].xml",
                _CONTENT_TYPES.format(
                    part="xl/workbook.xml",
                    content_type="application/vnd.ms-excel.sheet.macroEnabled.main+xml",
                    extra_overrides=worksheet_override,
                ),
            ),
            ("_rels/.rels", _RELATIONSHIPS.format(target="xl/workbook.xml")),
            ("xl/workbook.xml", _WORKBOOK),
            ("xl/_rels/workbook.xml.rels", _WORKBOOK_RELS),
            ("xl/worksheets/sheet1.xml", _WORKSHEET),
        ),
    )


if __name__ == "__main__":
    generate(Path(__file__).parent / "corpus")
