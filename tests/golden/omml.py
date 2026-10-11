"""Canonical OMML equation observations for DOCX and PPTX golden tests (T103)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

from tests.golden.limits import ArchiveLimits
from tests.golden.openxml import OpenXmlError, inspect_docx

MATH_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/math"
PANDOC_VERSION = "3.10.2"
OMML_GOLDEN_SCHEMA_VERSION = 1
GOLDEN_DOCX_LIMITS = ArchiveLimits(200, 5_000_000, 20_000_000, 200.0)
_MATH = f"{{{MATH_NAMESPACE}}}oMath"
_MATH_PARAGRAPH = f"{{{MATH_NAMESPACE}}}oMathPara"
_MATH_TEXT = f"{{{MATH_NAMESPACE}}}t"


@dataclass(frozen=True)
class OmmlEquation:
    """One native Office equation in document order."""

    display: bool
    text: str
    canonical_xml: str

    def as_json(self) -> dict[str, object]:
        return {
            "display": self.display,
            "text": self.text,
            "xml": self.canonical_xml,
        }


def _parse(part_xml: str | bytes, part_name: str) -> ElementTree.Element:
    data = part_xml.encode("utf-8") if isinstance(part_xml, str) else part_xml
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise OpenXmlError(f"DTD and entity declarations are forbidden: {part_name}")
    try:
        # ElementTree does not dereference external entities; declarations are rejected above.
        return ElementTree.fromstring(data)  # noqa: S314
    except ElementTree.ParseError as error:
        raise OpenXmlError(f"Invalid XML part: {part_name}") from error


def _equation(element: ElementTree.Element, *, display: bool) -> OmmlEquation:
    serialized = ElementTree.tostring(element, encoding="unicode")
    return OmmlEquation(
        display=display,
        text="".join(node.text or "" for node in element.iter(_MATH_TEXT)),
        canonical_xml=ElementTree.canonicalize(
            serialized, strip_text=False, rewrite_prefixes=True
        ),
    )


def omml_equations(part_xml: str | bytes, part_name: str) -> tuple[OmmlEquation, ...]:
    """Return outermost display (`m:oMathPara`) and inline (`m:oMath`) equations."""

    equations: list[OmmlEquation] = []
    pending = [_parse(part_xml, part_name)]
    while pending:
        element = pending.pop()
        if element.tag == _MATH_PARAGRAPH:
            equations.append(_equation(element, display=True))
        elif element.tag == _MATH:
            equations.append(_equation(element, display=False))
        else:
            pending.extend(reversed(list(element)))
    return tuple(equations)


def omml_golden(
    equations: tuple[OmmlEquation, ...], source_markdown: bytes
) -> dict[str, object]:
    """Build the canonical JSON golden for equations produced from one source."""

    return {
        "equations": [equation.as_json() for equation in equations],
        "pandoc_version": PANDOC_VERSION,
        "schema_version": OMML_GOLDEN_SCHEMA_VERSION,
        "source_markdown_sha256": hashlib.sha256(source_markdown).hexdigest(),
    }


def golden_equations(golden: Mapping[str, object]) -> tuple[OmmlEquation, ...]:
    """Load the equations of a checked-in OMML golden."""

    raw = golden["equations"]
    if not isinstance(raw, list):
        raise ValueError("OMML golden equations must be a list")
    return tuple(
        OmmlEquation(
            display=bool(item["display"]),
            text=str(item["text"]),
            canonical_xml=str(item["xml"]),
        )
        for item in raw
    )


def generate(source: Path, output: Path) -> None:
    """Regenerate an OMML golden with the approved Pandoc in Pandoc-default mode."""

    from markweave.conversion.pandoc import (  # noqa: PLC0415 - optional CLI path
        PandocConfig,
        PandocDocxConverter,
    )
    from markweave.conversion.validation import (  # noqa: PLC0415
        ApprovedMarkdown,
    )

    pandoc = shutil.which("pandoc")
    if pandoc is None:
        raise RuntimeError("The approved Pandoc engine is required")
    detected = subprocess.run(
        [pandoc, "--version"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    ).stdout.splitlines()[0]
    if detected != f"pandoc {PANDOC_VERSION}":
        raise RuntimeError("The approved Pandoc version is required")
    markdown = source.read_bytes()
    with tempfile.TemporaryDirectory(prefix="md-converter-omml-") as workspace:
        docx = PandocDocxConverter(
            PandocConfig(pandoc, 30.0, 2.0, Path(workspace)), os.environ
        ).convert(ApprovedMarkdown(markdown.decode("utf-8")), None)
    snapshot = inspect_docx(docx, GOLDEN_DOCX_LIMITS)
    equations = omml_equations(
        snapshot.xml_parts["word/document.xml"], "word/document.xml"
    )
    output.write_text(
        json.dumps(omml_golden(equations, markdown), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    generate(arguments.source, arguments.output)


if __name__ == "__main__":
    main()
