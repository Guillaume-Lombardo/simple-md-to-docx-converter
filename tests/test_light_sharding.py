"""Partition and actual pytest boundary regressions for parallel light CI."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from pytest_mock import MockerFixture

from scripts.ci.light_sharding import (
    pytest_collection_modifyitems,
    pytest_configure,
    shard_for,
)


@pytest.mark.unit
def test_partition_is_stable_disjoint_and_exhaustive(mocker: MockerFixture) -> None:
    items = [
        cast(
            "pytest.Item", SimpleNamespace(nodeid=f"test_module.py::test_case[{index}]")
        )
        for index in range(1000)
    ]
    partitions = []
    for index in (0, 1):
        config = mocker.Mock(spec=pytest.Config)
        config.getoption.side_effect = lambda name, index=index: (
            index if name == "light_shard_index" else 2
        )
        config.hook = mocker.Mock()
        selected = list(items)
        pytest_configure(config)
        pytest_collection_modifyitems(config, selected)
        partitions.append({item.nodeid for item in selected})
        removed = config.hook.pytest_deselected.call_args.kwargs["items"]
        assert len(selected) + len(removed) == len(items)
    assert partitions[0] and partitions[1]
    assert not partitions[0] & partitions[1]
    assert partitions[0] | partitions[1] == {item.nodeid for item in items}
    assert shard_for("tests/test_example.py::test_one[value]", 2) == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    ("index", "count"), [(None, None), (-1, 2), (2, 2), (0, 0), (0, 3)]
)
def test_invalid_partition_fails_closed(
    mocker: MockerFixture, index: int | None, count: int | None
) -> None:
    config = mocker.Mock(spec=pytest.Config)
    config.getoption.side_effect = lambda name: (
        index if name == "light_shard_index" else count
    )
    with pytest.raises(pytest.UsageError):
        pytest_configure(config)


@pytest.mark.integration
@pytest.mark.light_coverage
def test_real_pytest_shards_respect_markers_and_invalid_arguments(
    tmp_path: Path,
) -> None:
    """Actual collection runs each selected test once and excludes other domains."""
    fixture = tmp_path / "test_partition_fixture.py"
    fixture.write_text(
        "import pytest\n"
        "@pytest.mark.unit\n"
        "@pytest.mark.parametrize('value', range(12))\n"
        "def test_unit(value): pass\n"
        "@pytest.mark.light_coverage\n"
        "def test_boundary(): pass\n"
        "def test_unselected(): raise AssertionError('not selected')\n"
    )
    base = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        "/dev/null",
        str(fixture),
        "-q",
        "-m",
        "unit or light_coverage",
        "-p",
        "scripts.ci.light_sharding",
        "--light-shard-count=2",
    ]
    environment = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    counts = []
    for index in (0, 1):
        result = subprocess.run(
            [*base, f"--light-shard-index={index}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        counts.append(int(result.stdout.split(" passed")[0].split()[-1]))
    assert sum(counts) == 13
    invalid = subprocess.run(
        [*base, "--light-shard-index=2"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    assert invalid.returncode == 4
    assert "requires shard count 2" in invalid.stderr


@pytest.mark.integration
@pytest.mark.light_coverage
def test_shard_generates_raw_coverage_without_enforcing_partial_threshold(
    tmp_path: Path,
) -> None:
    """The canonical local branch hook is disabled only in a partial CI job."""
    repository = Path.cwd()
    fixture = tmp_path / "test_partial_fixture.py"
    fixture.write_text(
        "import pytest\nfrom markweave import __version__\n@pytest.mark.unit\ndef test_version(): assert __version__\n"
    )
    nodeid = f"{fixture.name}::test_version"
    index = shard_for(nodeid, 2)
    environment = {
        **os.environ,
        "PYTHONPATH": str(repository),
        "COVERAGE_FILE": str(tmp_path / ".coverage"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(repository / "pyproject.toml"),
            str(fixture),
            "--rootdir",
            str(tmp_path),
            "-q",
            "-m",
            "unit or light_coverage",
            "-p",
            "no:scripts.ci.pytest_branch_coverage",
            "-p",
            "scripts.ci.light_sharding",
            "--light-shard-count=2",
            f"--light-shard-index={index}",
            "--cov-fail-under=0",
            "--cov-report=",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / ".coverage").is_file()
    assert not (tmp_path / "coverage.json").exists()
