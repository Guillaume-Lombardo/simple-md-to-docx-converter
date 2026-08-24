"""Pure schema and path-validation tests for corpus helpers."""

from __future__ import annotations

import copy
from collections.abc import Callable

import pytest

from tests.golden import corpus


def valid_case() -> dict[str, object]:
    return {
        "id": "case",
        "categories": ["unicode"],
        "kind": "generated_zip",
        "purpose": "purpose",
        "future_owner": "T07",
        "entrypoint": "case.zip",
        "files": ["case.zip"],
        "builder": "path_traversal_zip",
        "expected": {"result": "rejected"},
        "provenance": {
            "generator": "tests.golden.corpus.BUILDERS[path_traversal_zip]",
            "license": "CC0-1.0",
        },
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "nul\0path",
        "bad\\path",
        "//server/share",
        "/root",
        "../up",
        "C:/drive",
        "a//b",
    ],
)
def test_safe_corpus_path_rejects_each_unsafe_spelling(value: object) -> None:
    with pytest.raises(corpus.CorpusManifestError):
        corpus.safe_corpus_path(value, "path")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (None, "object"),
        (lambda raw: raw.update(id="bad id"), "case id"),
        (lambda raw: raw.update(categories=[]), "string categories"),
        (
            lambda raw: raw.update(categories=["unicode", "unicode"]),
            "unique and sorted",
        ),
        (lambda raw: raw.update(kind="unknown"), "unknown kind"),
        (lambda raw: raw.update(purpose=""), "purpose"),
        (lambda raw: raw.update(builder=42), "builder must be a string"),
        (lambda raw: raw.update(builder=None), "builder does not match"),
        (lambda raw: raw.update(files=["case.zip", "other.zip"]), "one output"),
        (
            lambda raw: raw.update(
                entrypoint="Case.zip", files=["Case.zip", "case.zip"]
            ),
            "collision-free",
        ),
        (lambda raw: raw.update(expected={}), "expected observations"),
        (lambda raw: raw.update(provenance={}), "generator and license"),
    ],
)
def test_case_schema_rejects_invalid_fields(
    mutation: Callable[[dict[str, object]], object] | None, message: str
) -> None:
    raw = copy.deepcopy(valid_case())
    candidate: object = None
    if mutation is not None:
        mutation(raw)
        candidate = raw
    with pytest.raises(corpus.CorpusManifestError, match=message):
        corpus._parse_case(candidate)
