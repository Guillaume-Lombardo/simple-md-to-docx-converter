"""Security regressions for the executable Kubernetes acceptance helpers."""

from __future__ import annotations

import runpy
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).parents[2]
RENDER_SCRIPT = ROOT / "scripts/e2e/render-kubernetes-reverse-acceptance.py"


def _private_writer() -> Callable[[Path, str], None]:
    namespace = runpy.run_path(str(RENDER_SCRIPT))
    return cast(Callable[[Path, str], None], namespace["_write_private_text"])


def test_kubernetes_renderer_creates_private_output_before_writing(
    tmp_path: Path,
) -> None:
    output = tmp_path / "deployment.yaml"

    _private_writer()(output, "private-key-material")

    assert output.read_text(encoding="utf-8") == "private-key-material"
    assert output.stat().st_mode & 0o777 == 0o600


def test_kubernetes_renderer_rejects_symlink_output(tmp_path: Path) -> None:
    target = tmp_path / "target.yaml"
    target.write_text("preserve", encoding="utf-8")
    output = tmp_path / "deployment.yaml"
    output.symlink_to(target)

    with pytest.raises(OSError):
        _private_writer()(output, "private-key-material")

    assert target.read_text(encoding="utf-8") == "preserve"
