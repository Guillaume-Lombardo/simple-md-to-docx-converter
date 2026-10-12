"""Fail any test session that collects a repository test without a category marker.

CI selects Python tests only by marker expression, for example
``-m "unit or light_coverage"`` for the light shards. A test without a category
marker therefore runs in no CI job, silently. This plugin is registered through
``addopts`` in ``pyproject.toml`` and checks every collected item under
``tests/`` before any marker, keyword or shard deselection runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

CATEGORY_MARKERS = ("unit", "functional", "integration", "e2e")


def uncategorized_node_ids(items: list[pytest.Item], tests_root: Path) -> list[str]:
    """Return the node IDs of items under ``tests_root`` without a category marker."""
    return [
        item.nodeid
        for item in items
        if item.path.is_relative_to(tests_root)
        and not any(item.get_closest_marker(name) for name in CATEGORY_MARKERS)
    ]


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Report a collection error before deselection can hide an uncategorized test.

    A failed collection report stops the session in a serial run and is
    forwarded by every pytest-xdist worker to the controller, which stops too.
    """
    markers = ", ".join(CATEGORY_MARKERS)
    for node_id in uncategorized_node_ids(items, config.rootpath / "tests"):
        config.hook.pytest_collectreport(
            report=pytest.CollectReport(
                node_id,
                "failed",
                f"{node_id} carries no category marker ({markers}), "
                "so no CI marker selection would ever run it.",
                [],
            )
        )
