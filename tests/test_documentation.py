"""Repository-wide contracts for the public documentation set."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

from markweave.config import Settings

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"!?\[[^]]*]\(([^)]+)\)")


def _documentation_files() -> tuple[Path, ...]:
    return (
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        *sorted((ROOT / "docs").rglob("*.md")),
    )


def _local_link_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().split(maxsplit=1)[0].strip("<>")
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path or parsed.path.startswith("/"):
        return None
    return (source.parent / unquote(parsed.path)).resolve()


def test_local_documentation_links_resolve_to_repository_files() -> None:
    broken: list[str] = []
    for source in _documentation_files():
        text = source.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = _local_link_target(source, raw_target)
            if target is not None and (
                ROOT not in target.parents or not target.is_file()
            ):
                broken.append(f"{source.relative_to(ROOT)} -> {raw_target}")

    assert broken == []


def test_configuration_reference_covers_every_runtime_setting() -> None:
    reference = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"`(MD_CONVERTER_[A-Z0-9_]+)`", reference))
    expected = {f"MD_CONVERTER_{name.upper()}" for name in Settings.model_fields}

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
