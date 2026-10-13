"""Tests for the guard that requires a category marker on every repository test."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tests import category_marker_guard

pytest_plugins = ["pytester"]
pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
MARKERS_INI = (
    "[pytest]\nmarkers =\n"
    "    unit: unit\n    functional: functional\n"
    "    integration: integration\n    e2e: e2e\n    other: other\n"
    "    light_coverage: light coverage\n"
)


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    pytester.makeini(MARKERS_INI)
    tests = pytester.mkdir("tests")
    (tests / "test_sample.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.unit\ndef test_unit():\n    pass\n\n"
        "@pytest.mark.other\ndef test_other_only():\n    pass\n\n"
        "class TestGroup:\n    def test_bare(self):\n        pass\n"
    )
    return pytester.runpytest(
        "-p", "no:cacheprovider", *args, plugins=[category_marker_guard]
    )


def test_guard_is_registered_for_every_repository_session() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]

    assert ["-p", "tests.category_marker_guard"] in [
        addopts[index : index + 2] for index in range(len(addopts) - 1)
    ]


@pytest.mark.parametrize("marker", category_marker_guard.CATEGORY_MARKERS)
def test_every_category_marker_satisfies_the_guard(
    pytester: pytest.Pytester, marker: str
) -> None:
    pytester.makeini(MARKERS_INI)
    tests = pytester.mkdir("tests")
    (tests / "test_sample.py").write_text(
        f"import pytest\n\npytestmark = pytest.mark.{marker}\n\n"
        "def test_x():\n    pass\n"
    )

    result = pytester.runpytest(
        "-p", "no:cacheprovider", plugins=[category_marker_guard]
    )

    result.assert_outcomes(passed=1)


def test_uncategorized_tests_fail_the_session_with_their_node_ids(
    pytester: pytest.Pytester,
) -> None:
    result = _run(pytester)

    assert result.ret == pytest.ExitCode.INTERRUPTED
    result.assert_outcomes(errors=2)
    result.stdout.fnmatch_lines(
        [
            "*tests/test_sample.py::test_other_only carries no category marker*",
            "*tests/test_sample.py::TestGroup::test_bare carries no category marker*",
        ]
    )


def test_marker_deselection_cannot_hide_an_uncategorized_test(
    pytester: pytest.Pytester,
) -> None:
    result = _run(pytester, "-m", "unit")

    assert result.ret == pytest.ExitCode.INTERRUPTED
    result.assert_outcomes(errors=2)


def test_tests_outside_the_repository_test_tree_are_not_checked(
    pytester: pytest.Pytester,
) -> None:
    pytester.makeini(MARKERS_INI)
    pytester.makepyfile(test_outside="def test_x():\n    pass\n")

    result = pytester.runpytest(
        "-p", "no:cacheprovider", plugins=[category_marker_guard]
    )

    result.assert_outcomes(passed=1)


def _run_light_shard_suite(
    pytester: pytest.Pytester, directory: str, markers: str
) -> pytest.RunResult:
    pytester.makeini(MARKERS_INI)
    suite = pytester.mkdir("tests")
    for part in directory.split("/")[1:]:
        suite = suite / part
        suite.mkdir()
    (suite / "test_suite.py").write_text(
        f"import pytest\n\npytestmark = [{markers}]\n\ndef test_x():\n    pass\n"
    )
    return pytester.runpytest("-p", "no:cacheprovider", plugins=[category_marker_guard])


@pytest.mark.parametrize("directory", category_marker_guard.LIGHT_SHARD_ONLY_PATHS)
def test_light_shard_only_suites_must_match_the_light_expression(
    pytester: pytest.Pytester, directory: str
) -> None:
    result = _run_light_shard_suite(
        pytester, directory, "pytest.mark.integration, pytest.mark.functional"
    )

    assert result.ret == pytest.ExitCode.INTERRUPTED
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(
        [f"*{directory}/test_suite.py::test_x is not selected by the light shards*"]
    )


@pytest.mark.parametrize(
    "markers",
    [
        "pytest.mark.integration, pytest.mark.light_coverage",
        "pytest.mark.functional, pytest.mark.light_coverage",
        "pytest.mark.unit",
    ],
    ids=["integration-light", "functional-light", "unit"],
)
def test_light_shard_only_suites_accept_light_markers(
    pytester: pytest.Pytester, markers: str
) -> None:
    result = _run_light_shard_suite(pytester, "tests/integration/sqlite", markers)

    result.assert_outcomes(passed=1)


def test_other_integration_suites_need_no_light_marker(
    pytester: pytest.Pytester,
) -> None:
    result = _run_light_shard_suite(
        pytester, "tests/integration/postgres", "pytest.mark.integration"
    )

    result.assert_outcomes(passed=1)


@pytest.mark.parametrize("selection", [(), ("-m", "unit")], ids=["all", "unit-only"])
def test_xdist_session_reports_the_node_and_runs_no_test_body(
    pytester: pytest.Pytester,
    monkeypatch: pytest.MonkeyPatch,
    selection: tuple[str, ...],
) -> None:
    pytester.makeini(MARKERS_INI)
    ran = pytester.path / "ran"
    tests = pytester.mkdir("tests")
    (tests / "test_sample.py").write_text(
        "import pathlib\n\nimport pytest\n\n"
        f"RAN = pathlib.Path({str(ran)!r})\n\n"
        "@pytest.mark.unit\ndef test_unit():\n    RAN.touch()\n\n"
        "def test_bare():\n    RAN.touch()\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(ROOT / "tests"))

    result = pytester.runpytest_subprocess(
        "-p",
        "category_marker_guard",
        "-p",
        "no:cacheprovider",
        "-n",
        "2",
        *selection,
    )

    assert result.ret == pytest.ExitCode.TESTS_FAILED
    result.stdout.fnmatch_lines(
        ["*tests/test_sample.py::test_bare carries no category marker*"]
    )
    result.assert_outcomes(errors=1)
    assert not ran.exists()
