"""Deterministic OMML observation helpers used by the T103 math golden tests."""

from __future__ import annotations

import hashlib

import pytest

from tests.golden.omml import (
    MATH_NAMESPACE,
    golden_equations,
    omml_equations,
    omml_golden,
)
from tests.golden.openxml import OpenXmlError

pytestmark = pytest.mark.unit
WORD = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PART = f"""<w:document xmlns:w="{WORD}" xmlns:m="{MATH_NAMESPACE}"><w:body>
<w:p><w:r><w:t>Before</w:t></w:r><m:oMath><m:r><m:t>a</m:t></m:r></m:oMath></w:p>
<w:p><m:oMathPara><m:oMath><m:r><m:t>b</m:t></m:r></m:oMath>
<m:oMath><m:r><m:t>c</m:t></m:r></m:oMath></m:oMathPara></w:p>
<w:p><m:oMath><m:r><m:t>d</m:t></m:r></m:oMath></w:p>
</w:body></w:document>"""


def test_outermost_equations_are_reported_in_document_order() -> None:
    equations = omml_equations(PART, "word/document.xml")

    assert [(item.display, item.text) for item in equations] == [
        (False, "a"),
        (True, "bc"),
        (False, "d"),
    ]
    assert equations[0].canonical_xml == (
        f'<n0:oMath xmlns:n0="{MATH_NAMESPACE}"><n0:r><n0:t>a</n0:t></n0:r></n0:oMath>'
    )


def test_prefix_spelling_and_declaration_site_do_not_change_observations() -> None:
    respelled = (
        f'<root><x:oMath xmlns:x="{MATH_NAMESPACE}"><x:r><x:t>a</x:t></x:r>'
        "</x:oMath></root>"
    )

    assert (
        omml_equations(respelled.encode(), "slide.xml")
        == omml_equations(PART, "word/document.xml")[:1]
    )


def test_golden_round_trip_records_source_and_pinned_pandoc() -> None:
    equations = omml_equations(PART, "word/document.xml")
    golden = omml_golden(equations, b"source")

    assert golden["pandoc_version"] == "3.10.2"
    assert golden["schema_version"] == 1
    assert golden["source_markdown_sha256"] == hashlib.sha256(b"source").hexdigest()
    assert golden_equations(golden) == equations


def test_golden_equations_must_be_a_list() -> None:
    with pytest.raises(ValueError, match="must be a list"):
        golden_equations({"equations": {}})


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('<!DOCTYPE x [<!ENTITY e "x">]><x>&e;</x>', "DTD and entity"),
        ("<unterminated>", "Invalid XML part"),
    ],
)
def test_unsafe_or_malformed_parts_are_rejected(payload: str, message: str) -> None:
    with pytest.raises(OpenXmlError, match=message):
        omml_equations(payload, "word/document.xml")
