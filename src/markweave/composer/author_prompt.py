"""Deterministic, reviewable disclosure of selected author values to a model."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class AuthorPromptSnapshot:
    id: UUID
    version: int
    name: str
    fields: dict[str, object]


def compose_author_prompt(
    content: str, authors: tuple[AuthorPromptSnapshot, ...]
) -> tuple[str, str]:
    """Return exact proposed outbound text and its approval digest.

    The caller must authorize every author immediately before constructing this
    snapshot, again before egress, and when the result is published. This helper
    has no access to the author directory or the transport.
    """

    if not content.strip():
        raise ValueError("Model content is empty")
    ids = [item.id for item in authors]
    if len(ids) != len(set(ids)):
        raise ValueError("An author was selected more than once")
    author_data = [
        {
            "id": str(item.id),
            "version": item.version,
            "name": item.name,
            "fields": item.fields,
        }
        for item in authors
    ]
    disclosure = json.dumps(
        author_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    transmitted = (
        f"{content}\n\nSelected author records (reviewed data; preserve provenance):\n"
        f"{disclosure}"
        if authors
        else content
    )
    digest = hashlib.sha256(transmitted.encode("utf-8")).hexdigest()
    return transmitted, digest
