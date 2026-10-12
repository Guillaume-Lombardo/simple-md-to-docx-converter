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
