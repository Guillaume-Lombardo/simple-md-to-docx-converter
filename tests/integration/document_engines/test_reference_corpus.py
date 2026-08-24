"""Filesystem, archive, and OpenXML boundary tests for the T04 corpus."""

import io
import shutil
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from tests.golden.corpus import (
    CORPUS_CATEGORIES,
    ArchiveInspectionLimits,
    CorpusManifest,
    CorpusManifestError,
    build_case_bytes,
    inspect_archive_fixture,
    read_manifest,
)
from tests.golden.openxml import OpenXmlError, compare_docx, inspect_docx

pytestmark = pytest.mark.integration

LIMITS = ArchiveInspectionLimits(20, 100_000, 500_000)


def test_manifest_covers_every_category_with_review_metadata(
    corpus_manifest: CorpusManifest,
) -> None:
    assert {
        category for case in corpus_manifest.cases for category in case.categories
    } == CORPUS_CATEGORIES
    assert all(
        case.purpose and case.future_owner and case.files
        for case in corpus_manifest.cases
    )


def test_two_generated_templates_are_distinct_deterministic_openxml(
    corpus_manifest: CorpusManifest,
) -> None:
    classic = corpus_manifest.by_id("template-classic")
    modern = corpus_manifest.by_id("template-modern")
    assert build_case_bytes(classic) == build_case_bytes(classic)
    classic_snapshot = inspect_docx(build_case_bytes(classic))
    modern_snapshot = inspect_docx(build_case_bytes(modern))
    assert classic_snapshot.style_ids == ("ClassicBody",)
    assert modern_snapshot.style_ids == ("ModernBody",)
    comparison = compare_docx(
        classic_snapshot, modern_snapshot, ignored_parts=frozenset()
    )
    assert not comparison.matches
    assert "word/document.xml" in comparison.changed_xml_parts


@pytest.mark.parametrize(
    ("case_id", "message"),
    [("archive-path-traversal", "safe relative"), ("archive-symlink", "symbolic-link")],
)
def test_malicious_zip_fixture_exists_and_is_rejected_without_extraction(
    corpus_manifest: CorpusManifest, case_id: str, message: str
) -> None:
    data = build_case_bytes(corpus_manifest.by_id(case_id))
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert len(archive.infolist()) == 2
    with pytest.raises(CorpusManifestError, match=message):
        inspect_archive_fixture(data, LIMITS)


def test_static_case_materialization_preserves_relative_local_resource(
    materialize_corpus_case,
) -> None:
    document = materialize_corpus_case("local-image")
    assert document.is_file()
    assert (document.parent / "assets/safe-local.svg").is_file()


@pytest.mark.parametrize(
    "unsafe",
    ["/absolute", "../escape", "C:/drive", "//server/share", "bad\\path", "nul\0path"],
)
def test_manifest_rejects_unsafe_paths(tmp_path: Path, unsafe: str) -> None:
    source = Path("tests/corpus/manifest.json").read_text(encoding="utf-8")
    source = source.replace('"archive-path-traversal.zip"', f'"{unsafe}"', 1)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(source, encoding="utf-8")
    with pytest.raises(CorpusManifestError):
        read_manifest(manifest)


def test_docx_rejects_dtd_before_xml_parsing(corpus_manifest: CorpusManifest) -> None:
    data = build_case_bytes(corpus_manifest.by_id("template-classic"))
    output = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(output, "w") as target,
    ):
        for member in source.infolist():
            payload = source.read(member)
            if member.filename == "word/document.xml":
                payload = payload.replace(
                    b"<w:document",
                    b"<!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]><w:document",
                )
            target.writestr(member, payload)
    with pytest.raises(OpenXmlError, match="DTD"):
        inspect_docx(output.getvalue())


def test_archive_limits_are_explicit_and_enforced(
    corpus_manifest: CorpusManifest,
) -> None:
    data = build_case_bytes(corpus_manifest.by_id("archive-symlink"))
    with pytest.raises(CorpusManifestError, match="entry cap"):
        inspect_archive_fixture(data, ArchiveInspectionLimits(1, 100_000, 500_000))


def test_archive_rejects_casefolded_duplicate_member_names() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("assets/Image.png", b"one")
        archive.writestr("assets/image.png", b"two")
    with pytest.raises(CorpusManifestError, match="duplicate normalized"):
        inspect_archive_fixture(output.getvalue(), LIMITS)


def test_docx_namespace_prefix_and_zip_metadata_do_not_affect_snapshot(
    corpus_manifest: CorpusManifest,
) -> None:
    original = build_case_bytes(corpus_manifest.by_id("template-classic"))
    rewritten = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(original)) as source,
        zipfile.ZipFile(rewritten, "w") as target,
    ):
        for member in reversed(source.infolist()):
            payload = source.read(member)
            if member.filename in {"word/document.xml", "word/styles.xml"}:
                payload = payload.replace(b"w:", b"word:").replace(
                    b"xmlns:w=", b"xmlns:word="
                )
            replacement = zipfile.ZipInfo(member.filename, (2026, 8, 24, 12, 0, 0))
            target.writestr(replacement, payload)
    assert inspect_docx(original) == inspect_docx(rewritten.getvalue())


def test_docx_comparison_reports_missing_extra_and_changed_media(
    corpus_manifest: CorpusManifest,
) -> None:
    snapshot = inspect_docx(build_case_bytes(corpus_manifest.by_id("template-classic")))
    changed = replace(
        snapshot,
        parts=(*snapshot.parts, "word/media/image.png"),
        binary_sha256={"word/media/image.png": "actual"},
    )
    expected = replace(
        snapshot,
        binary_sha256={"word/media/image.png": "expected"},
        parts=(*snapshot.parts, "word/media/image.png"),
    )
    assert compare_docx(
        snapshot, changed, ignored_parts=frozenset()
    ).unexpected_parts == ("word/media/image.png",)
    assert compare_docx(changed, snapshot, ignored_parts=frozenset()).missing_parts == (
        "word/media/image.png",
    )
    assert compare_docx(
        expected, changed, ignored_parts=frozenset()
    ).changed_binary_parts == ("word/media/image.png",)
    assert compare_docx(
        expected,
        changed,
        ignored_parts=frozenset({"word/media/image.png"}),
    ).matches


def test_manifest_rejects_static_symlink(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    shutil.copytree("tests/corpus", root)
    victim = root / "fonts/families.md"
    target = tmp_path / "outside.md"
    target.write_text("outside", encoding="utf-8")
    victim.unlink()
    victim.symlink_to(target)
    with pytest.raises(CorpusManifestError, match="symlink"):
        read_manifest(root / "manifest.json")
