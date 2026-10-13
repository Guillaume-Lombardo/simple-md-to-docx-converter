"""Fail any test session that collects a repository test CI would never select.

CI selects Python tests only by marker expression, for example
``-m "unit or light_coverage"`` for the light shards. A test without a category
marker therefore runs in no CI job, silently. The functional, authentication and
SQLite suites have no CI domain of their own: only the light shards run them, so
each of their tests must also match the light marker expression.

This plugin is registered through ``addopts`` in ``pyproject.toml`` and checks
every collected item under ``tests/`` before any marker, keyword or shard
deselection runs. On a violation it reports one collection error per offending
test and removes every collected item, so no test body runs: a serial session
stops with exit status 2 and a pytest-xdist session schedules nothing and exits
with status 1.
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
    """Abort the session before deselection can hide an unselected test.

    Each pytest-xdist worker forwards its failed collection reports to the
    controller and then offers it no item to schedule, so no worker runs a
    test either.
    """
    uncategorized = uncategorized_node_ids(items, config.rootpath / "tests")
    unselected = unselected_light_shard_node_ids(items, config.rootpath)
    categories = ", ".join(CATEGORY_MARKERS)
    for node_id in uncategorized:
        _report(
            config,
            node_id,
            f"carries no category marker ({categories}), "
            "so no CI marker selection would ever run it.",
        )
    light = " or ".join(LIGHT_SHARD_MARKERS)
    for node_id in unselected:
        _report(
            config,
            node_id,
            f"is not selected by the light shards ({light}), "
            "the only CI job that runs this suite.",
        )
    if uncategorized or unselected:
        items.clear()
