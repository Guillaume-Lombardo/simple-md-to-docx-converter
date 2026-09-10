from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
CONTAINERFILE = ROOT / "containers" / "kubernetes-attester" / "Containerfile"


@pytest.mark.unit
def test_attester_container_is_digest_pinned_and_has_a_content_free_entrypoint() -> (
    None
):
    source = CONTAINERFILE.read_text(encoding="utf-8")
    assert "ARG BASE_IMAGE=registry.access.redhat.com/ubi9/python-314@sha256:" in source
    assert "uv sync --locked --no-dev --no-editable --extra kubernetes" in source
    assert 'assert version("kubernetes") == "35.0.0"' in source
    assert "FROM scratch" in source
    assert "USER 0:0" in source
    assert 'ENTRYPOINT ["markweave-kubernetes-attester"]' in source
    assert "CMD" not in source
