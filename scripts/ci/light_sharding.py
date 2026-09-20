"""Deterministically partition the complete selected light suite across CI jobs."""

from __future__ import annotations

import hashlib

import pytest

SHARD_COUNT = 2


def shard_for(nodeid: str, count: int) -> int:
    """Assign each stable test identity to exactly one shard."""
    return int.from_bytes(hashlib.sha256(nodeid.encode()).digest()[:8]) % count


def pytest_addoption(parser: pytest.Parser) -> None:
    """Require an explicit bounded partition when this plugin is loaded."""
    parser.addoption("--light-shard-index", type=int)
    parser.addoption("--light-shard-count", type=int)


def pytest_configure(config: pytest.Config) -> None:
    """Fail before running tests when the partition is invalid."""
    index = config.getoption("light_shard_index")
    count = config.getoption("light_shard_count")
    if count != SHARD_COUNT or index not in range(SHARD_COUNT):
        raise pytest.UsageError("Light CI requires shard count 2 and index 0 or 1.")


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Partition after ordinary marker selection without losing selected tests."""
    index = config.getoption("light_shard_index")
    count = config.getoption("light_shard_count")
    selected = []
    deselected = []
    for item in items:
        (selected if shard_for(item.nodeid, count) == index else deselected).append(
            item
        )
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
