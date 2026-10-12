"""Fail any test session that collects a repository test CI would never select.

CI selects Python tests only by marker expression, for example
``-m "unit or light_coverage"`` for the light shards. A test without a category
marker therefore runs in no CI job, silently. The functional, authentication and
SQLite suites have no CI domain of their own: only the light shards run them, so
each of their tests must also match the light marker expression.

This plugin is registered through ``addopts`` in ``pyproject.toml`` and checks
every collected item under ``tests/`` before any marker, keyword or shard
deselection runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

CATEGORY_MARKERS = ("unit", "functional", "integration", "e2e")
LIGHT_SHARD_MARKERS = ("unit", "light_coverage")
LIGHT_SHARD_ONLY_PATHS = (
    "tests/functional",
    "tests/integration/auth",
    "tests/integration/sqlite",
)


def _has_marker(item: pytest.Item, names: tuple[str, ...]) -> bool:
    return any(item.get_closest_marker(name) for name in names)


def uncategorized_node_ids(items: list[pytest.Item], tests_root: Path) -> list[str]:
    """Return the node IDs of items under ``tests_root`` without a category marker."""
    return [
        item.nodeid
        for item in items
        if item.path.is_relative_to(tests_root)
        and not _has_marker(item, CATEGORY_MARKERS)
    ]


def unselected_light_shard_node_ids(items: list[pytest.Item], root: Path) -> list[str]:
    """Return light-shard-only items that the light marker expression misses."""
    paths = tuple(root / path for path in LIGHT_SHARD_ONLY_PATHS)
    return [
        item.nodeid
        for item in items
        if any(item.path.is_relative_to(path) for path in paths)
        and not _has_marker(item, LIGHT_SHARD_MARKERS)
    ]


def _report(config: pytest.Config, node_id: str, reason: str) -> None:
    config.hook.pytest_collectreport(
        report=pytest.CollectReport(node_id, "failed", f"{node_id} {reason}", [])
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Report a collection error before deselection can hide an unselected test.

    A failed collection report stops the session in a serial run and is
    forwarded by every pytest-xdist worker to the controller, which stops too.
    """
    categories = ", ".join(CATEGORY_MARKERS)
    for node_id in uncategorized_node_ids(items, config.rootpath / "tests"):
        _report(
            config,
            node_id,
            f"carries no category marker ({categories}), "
            "so no CI marker selection would ever run it.",
        )
    light = " or ".join(LIGHT_SHARD_MARKERS)
    for node_id in unselected_light_shard_node_ids(items, config.rootpath):
        _report(
            config,
            node_id,
            f"is not selected by the light shards ({light}), "
            "the only CI job that runs this suite.",
        )
