"""Repository-wide contracts for the public documentation set."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from markdown_it import MarkdownIt
from markdown_it.token import Token

from markweave.config import Settings

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = MarkdownIt()


def _documentation_files() -> tuple[Path, ...]:
    return (
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        *sorted((ROOT / "docs").rglob("*.md")),
    )


def _markdown_targets(text: str) -> tuple[str, ...]:
    targets: list[str] = []
    for token in MARKDOWN.parse(text):
        for child in token.children or ():
            attribute = {"link_open": "href", "image": "src"}.get(child.type)
            target = child.attrGet(attribute) if attribute is not None else None
            if isinstance(target, str) and target:
                targets.append(target)
    return tuple(targets)


def _local_link_target(source: Path, raw_target: str) -> tuple[Path, str] | None:
    parsed = urlsplit(raw_target)
    if parsed.scheme or parsed.netloc or parsed.path.startswith("/"):
        return None
    if not parsed.path and not parsed.fragment:
        return None
    path = source if not parsed.path else source.parent / unquote(parsed.path)
    return path.resolve(), unquote(parsed.fragment)


def _heading_text(token: Token) -> str:
    return "".join(
        child.content
        for child in token.children or ()
        if child.type in {"text", "code_inline", "image"}
    )


def _heading_slug(text: str) -> str:
    lowered = text.lower()
    characters = (
        character
        for character in lowered
        if character in "-_"
        or character.isspace()
        or character.isalnum()
        or unicodedata.category(character).startswith("M")
    )
    return re.sub(r"\s", "-", "".join(characters))


def _heading_fragments(path: Path) -> set[str]:
    tokens = MARKDOWN.parse(path.read_text(encoding="utf-8"))
    used: set[str] = set()
    next_suffix: dict[str, int] = {}
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or tokens[index + 1].type != "inline":
            continue
        base = _heading_slug(_heading_text(tokens[index + 1]))
        candidate = base
        suffix = next_suffix.get(base, 1)
        while candidate in used:
            candidate = f"{base}-{suffix}"
            suffix += 1
        used.add(candidate)
        next_suffix[base] = suffix
    return used


def _documentation_link_failures(files: tuple[Path, ...], root: Path) -> list[str]:
    broken: list[str] = []
    fragment_cache: dict[Path, set[str]] = {}
    for source in files:
        for raw_target in _markdown_targets(source.read_text(encoding="utf-8")):
            resolved = _local_link_target(source, raw_target)
            if resolved is None:
                continue
            target, fragment = resolved
            label = f"{source.relative_to(root)} -> {raw_target}"
            if root not in target.parents or not target.is_file():
                broken.append(label)
                continue
            if fragment and target.suffix.casefold() == ".md":
                if target not in fragment_cache:
                    fragment_cache[target] = _heading_fragments(target)
                headings = fragment_cache[target]
                if fragment not in headings:
                    broken.append(f"{label} (missing fragment)")
    return broken


def test_local_documentation_links_resolve_to_repository_files() -> None:
    assert _documentation_link_failures(_documentation_files(), ROOT) == []


def test_local_link_validation_covers_references_images_and_fragments(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    target.write_text(
        "# Target heading\n\n## Repeated\n\n## Repeated\n", encoding="utf-8"
    )
    source.write_text(
        "[reference][target]\n\n![image][asset]\n\n"
        "[duplicate](target.md#repeated-1)\n\n"
        "[target]: target.md#target-heading\n"
        "[asset]: image.png\n",
        encoding="utf-8",
    )
    (tmp_path / "image.png").write_bytes(b"image")

    assert _documentation_link_failures((source, target), tmp_path) == []

    source.write_text("[broken](target.md#missing-heading)\n", encoding="utf-8")
    assert _documentation_link_failures((source, target), tmp_path) == [
        "source.md -> target.md#missing-heading (missing fragment)"
    ]


def test_configuration_reference_covers_every_runtime_setting() -> None:
    reference = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"`(MARKWEAVE_[A-Z0-9_]+)`", reference))
    expected = {
        *(f"MARKWEAVE_{name.upper()}" for name in Settings.model_fields),
    }

    assert documented == expected


def test_documentation_index_exposes_all_role_guides() -> None:
    index = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")

    for guide in (
        "user-guide.md",
        "api-guide.md",
        "operations.md",
        "configuration.md",
        "recovery.md",
        "container-deployment.md",
        "architecture.md",
        "local-development.md",
        "agent-workflow.md",
    ):
        assert f"({guide})" in index


def test_local_development_links_the_package_management_guide() -> None:
    local_development = (ROOT / "docs" / "local-development.md").read_text(
        encoding="utf-8"
    )
    assert "[JavaScript package-management guide](package-management.md)" in (
        local_development
    )


def test_frontend_guides_use_unambiguous_workspace_commands() -> None:
    administration = (ROOT / "docs" / "administration-ui.md").read_text(
        encoding="utf-8"
    )
    assert "pnpm --filter @markweave/web run test:coverage" in administration
    assert "npm --prefix web" not in administration

    architecture = (ROOT / "docs" / "nextjs-migration-architecture.md").read_text(
        encoding="utf-8"
    )
    runtime = architecture.split("## Runtime image and rootless boundary", 1)[1]
    assert "Application builds do not use that bundled npm" in runtime
    assert "No package-manager binary or package-manager cache is present" in runtime
