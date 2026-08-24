"""Filesystem, archive, and OpenXML boundary tests for the T04 corpus."""

import io
import json
import shutil
import stat
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from tests.golden.corpus import (
    CORPUS_CATEGORIES,
    CorpusManifest,
    CorpusManifestError,
    build_case_bytes,
    inspect_archive_fixture,
    materialize_case,
    read_manifest,
)
from tests.golden.limits import ArchiveLimits
from tests.golden.openxml import OpenXmlError, compare_docx, inspect_docx

pytestmark = pytest.mark.integration

LIMITS = ArchiveLimits(20, 100_000, 500_000, 100.0)
ZipEntry = tuple[zipfile.ZipInfo | str, bytes]
ZipTransform = Callable[[zipfile.ZipInfo, bytes], ZipEntry | None]


def zip_bytes(entries: list[ZipEntry]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for member, payload in entries:
            archive.writestr(member, payload)
    return output.getvalue()


def rewrite_docx(
    data: bytes,
    *,
    transform: ZipTransform | None = None,
    extras: list[ZipEntry] | None = None,
) -> bytes:
    entries: list[ZipEntry] = []
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        for member in source.infolist():
            payload = source.read(member)
            if transform is not None:
                replacement = transform(member, payload)
                if replacement is None:
                    continue
                output_member, payload = replacement
            else:
                output_member = member
            entries.append((output_member, payload))
    entries.extend(extras or [])
    return zip_bytes(entries)


def mark_first_zip_member_encrypted(data: bytes) -> bytes:
    """Set the encryption flag in local and central headers for a rejection fixture."""

    changed = bytearray(data)
    local = changed.index(b"PK\x03\x04")
    central = changed.index(b"PK\x01\x02")
    local_flags = int.from_bytes(changed[local + 6 : local + 8], "little") | 1
    central_flags = int.from_bytes(changed[central + 8 : central + 10], "little") | 1
    changed[local + 6 : local + 8] = local_flags.to_bytes(2, "little")
    changed[central + 8 : central + 10] = central_flags.to_bytes(2, "little")
    return bytes(changed)


def copied_manifest(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    root = tmp_path / "corpus"
    shutil.copytree("tests/corpus", root)
    path = root / "manifest.json"
    return path, json.loads(path.read_text(encoding="utf-8"))


def write_manifest(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


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
    classic_snapshot = inspect_docx(build_case_bytes(classic), LIMITS)
    modern_snapshot = inspect_docx(build_case_bytes(modern), LIMITS)
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
        inspect_docx(output.getvalue(), LIMITS)


def test_archive_limits_are_explicit_and_enforced(
    corpus_manifest: CorpusManifest,
) -> None:
    data = build_case_bytes(corpus_manifest.by_id("archive-symlink"))
    with pytest.raises(CorpusManifestError, match="entry cap"):
        inspect_archive_fixture(data, ArchiveLimits(1, 100_000, 500_000, 100.0))


def test_archive_rejects_casefolded_duplicate_member_names() -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("assets/Image.png", b"one")
        archive.writestr("assets/image.png", b"two")
    with pytest.raises(CorpusManifestError, match="duplicate normalized"):
        inspect_archive_fixture(output.getvalue(), LIMITS)


def test_archive_accepts_safe_members_and_directories() -> None:
    assert inspect_archive_fixture(
        zip_bytes([("assets/", b""), ("assets/image.png", b"safe")]), LIMITS
    ) == ("assets/", "assets/image.png")


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
    assert inspect_docx(original, LIMITS) == inspect_docx(rewritten.getvalue(), LIMITS)


def test_docx_comparison_reports_missing_extra_and_changed_media(
    corpus_manifest: CorpusManifest,
) -> None:
    snapshot = inspect_docx(
        build_case_bytes(corpus_manifest.by_id("template-classic")), LIMITS
    )
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


def test_generated_case_materialization_reproduces_manifest_digest(
    materialize_corpus_case, corpus_manifest: CorpusManifest
) -> None:
    output = materialize_corpus_case("template-classic")
    case = corpus_manifest.by_id("template-classic")
    assert output.read_bytes() == build_case_bytes(case)


def test_manifest_lookup_and_materialization_failures(
    tmp_path: Path, corpus_manifest: CorpusManifest
) -> None:
    with pytest.raises(KeyError):
        corpus_manifest.by_id("absent")
    local = corpus_manifest.by_id("local-image")
    unrelated = replace(local, files=(*local.files, PurePosixPath("fonts/families.md")))
    with pytest.raises(CorpusManifestError, match="share the entrypoint directory"):
        materialize_case(corpus_manifest, unrelated, tmp_path)


def test_archive_rejects_invalid_and_encrypted_zip() -> None:
    with pytest.raises(CorpusManifestError, match="valid ZIP"):
        inspect_archive_fixture(b"not a zip", LIMITS)
    encrypted = mark_first_zip_member_encrypted(zip_bytes([("file.txt", b"data")]))
    with pytest.raises(CorpusManifestError, match="encrypted"):
        inspect_archive_fixture(encrypted, LIMITS)


@pytest.mark.parametrize(
    ("entries", "limits", "message"),
    [
        ([("one", b"1234")], ArchiveLimits(2, 3, 20, 100.0), "member"),
        ([("one", b"1234"), ("two", b"5678")], ArchiveLimits(2, 10, 7, 100.0), "total"),
        (
            [("zeros", bytes(10_000))],
            ArchiveLimits(2, 20_000, 20_000, 2.0),
            "compression-ratio",
        ),
    ],
)
def test_archive_preflight_rejects_member_total_and_ratio_caps(
    entries: list[ZipEntry], limits: ArchiveLimits, message: str
) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for member, payload in entries:
            archive.writestr(member, payload)
    with pytest.raises(CorpusManifestError, match=message):
        inspect_archive_fixture(output.getvalue(), limits)


def test_archive_limits_validate_all_fields() -> None:
    with pytest.raises(ValueError, match="positive"):
        ArchiveLimits(0, 1, 1, 1.0)
    with pytest.raises(ValueError, match="at least 1"):
        ArchiveLimits(1, 1, 1, 0.9)
    with pytest.raises(ValueError, match="finite"):
        ArchiveLimits(1, 1, 1, float("inf"))


def test_docx_rejects_invalid_zip() -> None:
    with pytest.raises(OpenXmlError, match="valid ZIP"):
        inspect_docx(b"not a zip", LIMITS)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unsafe", "unsafe"),
        ("unsafe-root", "unsafe"),
        ("duplicate", "duplicate normalized"),
        ("encrypted", "encrypted"),
        ("symlink", "symbolic-link"),
        ("missing", "missing required"),
        ("malformed", "Invalid XML"),
        ("page-size", "invalid page size"),
        ("relationship", "missing required metadata"),
    ],
)
def test_docx_rejects_unsafe_package_and_xml_boundaries(
    corpus_manifest: CorpusManifest, mutation: str, message: str
) -> None:
    base = build_case_bytes(corpus_manifest.by_id("template-classic"))
    if mutation == "unsafe":
        data = rewrite_docx(base, extras=[("../escape", b"bad")])
    elif mutation == "unsafe-root":
        data = rewrite_docx(base, extras=[("/absolute", b"bad")])
    elif mutation == "duplicate":
        data = rewrite_docx(base, extras=[("WORD/document.xml", b"<x/>")])
    elif mutation == "encrypted":
        data = mark_first_zip_member_encrypted(base)
    elif mutation == "symlink":
        link = zipfile.ZipInfo("word/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        data = rewrite_docx(base, extras=[(link, b"target")])
    elif mutation == "missing":
        data = rewrite_docx(
            base,
            transform=lambda member, payload: (
                None if member.filename == "word/document.xml" else (member, payload)
            ),
        )
    elif mutation == "malformed":
        data = rewrite_docx(
            base,
            transform=lambda member, payload: (
                (
                    member,
                    b"<broken",
                )
                if member.filename == "word/document.xml"
                else (member, payload)
            ),
        )
    elif mutation == "page-size":
        data = rewrite_docx(
            base,
            transform=lambda member, payload: (
                member,
                payload.replace(b'w:w="12240"', b'w:w="invalid"'),
            ),
        )
    else:
        data = rewrite_docx(
            base,
            transform=lambda member, payload: (
                member,
                payload.replace(b' Target="word/document.xml"', b""),
            ),
        )
    with pytest.raises(OpenXmlError, match=message):
        inspect_docx(data, LIMITS)


@pytest.mark.parametrize(
    ("limits", "message"),
    [
        (ArchiveLimits(3, 100_000, 500_000, 100.0), "entry cap"),
        (ArchiveLimits(20, 100, 500_000, 100.0), "part"),
        (ArchiveLimits(20, 100_000, 200, 100.0), "total"),
        (ArchiveLimits(20, 100_000, 500_000, 1.1), "compression-ratio"),
    ],
)
def test_docx_preflight_enforces_every_archive_limit(
    corpus_manifest: CorpusManifest, limits: ArchiveLimits, message: str
) -> None:
    data = build_case_bytes(corpus_manifest.by_id("template-classic"))
    with pytest.raises(OpenXmlError, match=message):
        inspect_docx(data, limits)


def test_docx_inspection_hashes_binary_media(corpus_manifest: CorpusManifest) -> None:
    base = build_case_bytes(corpus_manifest.by_id("template-classic"))
    snapshot = inspect_docx(
        rewrite_docx(base, extras=[("word/media/image.png", b"pixels")]), LIMITS
    )
    assert set(snapshot.binary_sha256) == {"word/media/image.png"}


def test_unknown_builder_and_non_generated_case_are_rejected(
    corpus_manifest: CorpusManifest,
) -> None:
    generated = corpus_manifest.by_id("template-classic")
    with pytest.raises(CorpusManifestError, match="unknown builder"):
        build_case_bytes(replace(generated, builder="absent"))
    static = corpus_manifest.by_id("font-families")
    with pytest.raises(CorpusManifestError, match="not generated"):
        build_case_bytes(static)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "0" * 64, "stale sha256"),
        ("size_bytes", 0, "stale size_bytes"),
        ("generator", "not-resolvable", "BUILDERS entry"),
    ],
)
def test_manifest_rejects_stale_or_unresolvable_generated_provenance(
    tmp_path: Path, field: str, value: object, message: str
) -> None:
    path, data = copied_manifest(tmp_path)
    cases = data["cases"]
    assert isinstance(cases, list)
    first = cases[0]
    assert isinstance(first, dict)
    provenance = first["provenance"]
    assert isinstance(provenance, dict)
    provenance[field] = value
    write_manifest(path, data)
    with pytest.raises(CorpusManifestError, match=message):
        read_manifest(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda data: data.update(version=2), "version"),
        (lambda data: data.update(cases=[]), "contain cases"),
        (
            lambda data: data["cases"][0].update(categories=["unknown"]),
            "unknown categories",
        ),
        (lambda data: data["cases"][0].update(future_owner="T99"), "future_owner"),
        (lambda data: data["cases"][0].update(files=[]), "declare files"),
        (
            lambda data: data["cases"][0].update(entrypoint="other.zip"),
            "listed in files",
        ),
    ],
)
def test_manifest_rejects_invalid_schema(
    tmp_path: Path, mutate: Callable[[dict], object], message: str
) -> None:
    path, data = copied_manifest(tmp_path)
    mutate(data)
    write_manifest(path, data)
    with pytest.raises(CorpusManifestError, match=message):
        read_manifest(path)


def test_manifest_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(CorpusManifestError, match="valid UTF-8 JSON"):
        read_manifest(path)


@pytest.mark.parametrize("mode", ["missing", "directory"])
def test_manifest_rejects_non_regular_static_file(tmp_path: Path, mode: str) -> None:
    path, data = copied_manifest(tmp_path)
    cases = data["cases"]
    assert isinstance(cases, list)
    fixture = path.parent / "fonts/families.md"
    fixture.unlink()
    if mode == "directory":
        fixture.mkdir()
    write_manifest(path, data)
    with pytest.raises(CorpusManifestError, match=r"does not exist|regular file"):
        read_manifest(path)


@pytest.mark.parametrize(
    "mode", ["unsorted", "duplicate", "missing-category", "path-collision"]
)
def test_manifest_rejects_cross_case_ambiguity(tmp_path: Path, mode: str) -> None:
    path, data = copied_manifest(tmp_path)
    cases = data["cases"]
    assert isinstance(cases, list)
    if mode == "unsorted":
        cases.reverse()
        message = "unique and sorted"
    elif mode == "duplicate":
        cases[1]["id"] = cases[0]["id"]
        message = "unique and sorted"
    elif mode == "missing-category":
        for case in cases:
            case["categories"] = ["unicode"]
        message = "categories are missing"
    else:
        font_case = next(case for case in cases if case["id"] == "font-families")
        timeout_case = next(case for case in cases if case["id"] == "timeout-engine")
        timeout_case["entrypoint"] = font_case["entrypoint"]
        timeout_case["files"] = font_case["files"]
        message = "path collision"
    write_manifest(path, data)
    with pytest.raises(CorpusManifestError, match=message):
        read_manifest(path)
