"""Tests for the runtime guard that requires markers on container runtime launches."""

from __future__ import annotations

import pytest

from tests.container_runtime_guard import (
    ContainerRuntimeGuard,
    launched_runtimes,
    shell_runtimes,
)

pytest_plugins = ["pytester"]
pytestmark = pytest.mark.unit

MISSING = "/nonexistent/bin/podman"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("podman ps", {"podman"}),
        ("/usr/bin/docker compose config", {"docker"}),
        ("cd /tmp && A=1 env podman ps", {"podman"}),
        ("echo ok; podman ps | wc -l", {"podman"}),
        ("true || nohup podman ps &", {"podman"}),
        ("set -e\npodman ps", {"podman"}),
        ("x=$(docker ps)", {"docker"}),
        ("(podman ps)", {"podman"}),
        ("sudo -n podman ps", {"podman"}),
        ('bash -c "podman ps"', {"podman"}),
        ("echo 'ok; podman ps'", set()),
        ('echo "podman ps"', set()),
        ("echo podman", set()),
        ("grep podman log | sort", set()),
        ("podman() { echo fake; }; podman ps", set()),
        ("function podman { echo fake; }; podman ps", set()),
        ('echo "unbalanced', set()),
    ],
)
def test_shell_commands_are_inspected_only_at_command_positions(
    command: str, expected: set[str]
) -> None:
    assert shell_runtimes(command) == expected


@pytest.mark.parametrize(
    ("event", "arguments", "expected"),
    [
        ("subprocess.Popen", ("podman", ["podman", "ps"], None, None), {"podman"}),
        ("subprocess.Popen", (MISSING, [MISSING, "ps"], None, None), {"podman"}),
        ("subprocess.Popen", ("/usr/bin/podman", ["echo"], None, None), {"podman"}),
        ("subprocess.Popen", ("/bin/echo", ["podman"], None, None), {"podman"}),
        (
            "subprocess.Popen",
            ("/bin/sh", ["/bin/sh", "-c", "podman ps"], None, None),
            {"podman"},
        ),
        (
            "subprocess.Popen",
            ("env", ["env", "A=1", "docker", "ps"], None, {}),
            {"docker"},
        ),
        (
            "subprocess.Popen",
            (b"/usr/bin/podman", [b"/usr/bin/podman"], None, None),
            {"podman"},
        ),
        ("subprocess.Popen", ("/bin/echo", ["echo", "podman"], None, None), set()),
        (
            "subprocess.Popen",
            ("/bin/sh", ["/bin/sh", "-c", "echo podman"], None, None),
            set(),
        ),
        ("os.system", (b"podman ps",), {"podman"}),
        ("os.system", ("echo 'podman ps'",), set()),
        ("os.exec", ("/usr/bin/podman", ["podman", "ps"], {}), {"podman"}),
        ("os.posix_spawn", ("/usr/bin/docker", ["docker"], {}), {"docker"}),
        ("pty.spawn", (["podman", "ps"],), {"podman"}),
        ("pty.spawn", ("podman",), {"podman"}),
        ("open", ("/usr/bin/podman", "r", 0), set()),
    ],
)
def test_audit_events_map_to_launched_runtimes(
    event: str, arguments: tuple[object, ...], expected: set[str]
) -> None:
    assert launched_runtimes(event, arguments) == expected


@pytest.fixture
def inner(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeini(
        "[pytest]\nmarkers =\n"
        "    requires_podman: requires Podman\n"
        "    requires_docker: requires Docker\n"
        "    requires_postgres: requires PostgreSQL\n"
    )
    return pytester


def _run(inner: pytest.Pytester, source: str) -> pytest.RunResult:
    inner.makepyfile(test_sample=source)
    return inner.runpytest("-p", "no:cacheprovider", plugins=[ContainerRuntimeGuard()])


GUARD_ERROR = "ContainerRuntimeMarkerError"
NOT_FOUND = f"No such file or directory: '{MISSING}'"


@pytest.mark.parametrize(
    "body",
    [
        f'import subprocess\n\ndef test_x():\n    subprocess.run(["{MISSING}", "ps"])\n',
        f'from subprocess import run as launch\n\ndef test_x():\n    launch(["{MISSING}"])\n',
        "import functools, subprocess\n\ndef test_x():\n"
        f'    functools.partial(subprocess.run, ["{MISSING}"])()\n',
        "import subprocess\n\ndef _run(*command):\n    return subprocess.run(command)\n\n"
        f'def test_x():\n    _run("{MISSING}", "ps")\n',
        f'import os\n\ndef test_x():\n    os.system("cd /tmp && {MISSING} ps")\n',
        "import asyncio\n\ndef test_x():\n"
        f'    asyncio.run(asyncio.create_subprocess_exec("{MISSING}", "ps"))\n',
        "import subprocess\n\ndef test_x():\n"
        f'    subprocess.run("echo ok; {MISSING} ps", shell=True)\n',
        "import pytest, subprocess\n\n@pytest.fixture\ndef image():\n"
        f'    subprocess.run(["{MISSING}", "build"])\n\n'
        "def test_x(image):\n    pass\n",
    ],
    ids=[
        "subprocess-run",
        "from-import-alias",
        "functools-partial",
        "helper-forwarding",
        "os-system",
        "asyncio-exec",
        "shell-true",
        "fixture-setup",
    ],
)
def test_unmarked_launch_fails_before_the_process_starts(
    inner: pytest.Pytester, body: str
) -> None:
    result = _run(inner, body)

    result.assert_outcomes(**({"errors": 1} if "fixture" in body else {"failed": 1}))
    result.stdout.fnmatch_lines([f"*{GUARD_ERROR}*launched podman without*"])
    result.stdout.no_fnmatch_line(f"*{NOT_FOUND}*")


def test_swallowed_guard_error_still_fails_the_test(inner: pytest.Pytester) -> None:
    result = _run(
        inner,
        "import subprocess\n\ndef test_x():\n    try:\n"
        f'        subprocess.run(["{MISSING}"])\n'
        "    except Exception:\n        pass\n",
    )

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*Container runtime launched without its marker:*", "*launched podman*"]
    )


@pytest.mark.parametrize(
    "body",
    [
        "import pytest, subprocess\n\n@pytest.mark.requires_podman\n"
        f'def test_x():\n    subprocess.run(["{MISSING}"])\n',
        "import pytest, subprocess\n\npytestmark = pytest.mark.requires_podman\n\n"
        f'class TestX:\n    def test_x(self):\n        subprocess.run(["{MISSING}"])\n',
        "import pytest, subprocess\n\n@pytest.mark.parametrize(\n"
        '    "name", [pytest.param("ps", marks=pytest.mark.requires_podman)]\n)\n'
        f'def test_x(name):\n    subprocess.run(["{MISSING}", name])\n',
    ],
    ids=["function-mark", "module-mark", "parameter-mark"],
)
def test_marked_launch_is_not_blocked(inner: pytest.Pytester, body: str) -> None:
    result = _run(inner, body)

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([f"*FileNotFoundError*{NOT_FOUND}*"])
    result.stdout.no_fnmatch_line(f"*{GUARD_ERROR}*")


def test_only_the_marked_parameter_case_may_launch(inner: pytest.Pytester) -> None:
    result = _run(
        inner,
        "import pytest, subprocess\n\n@pytest.mark.parametrize(\n"
        '    "name", [pytest.param("a", marks=pytest.mark.requires_podman), "b"]\n)\n'
        f'def test_x(name):\n    subprocess.run(["{MISSING}", name])\n',
    )

    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(
        [f"*{GUARD_ERROR}: test_sample.py::test_x?b? launched*"]
    )
    result.stdout.no_fnmatch_line("*test_sample.py::test_x?a? launched*")


@pytest.mark.parametrize(
    "body",
    [
        'import subprocess\n\ndef test_x():\n    subprocess.run(["echo", "podman"])\n',
        "import os\n\ndef test_x():\n    os.system(\"echo 'ok; podman ps' >/dev/null\")\n",
        "import pytest, subprocess\n\n@pytest.mark.requires_postgres\ndef test_x():\n"
        "    with pytest.raises(FileNotFoundError):\n"
        '        subprocess.run(["/nonexistent/bin/docker"])\n',
    ],
    ids=["echo-mention", "quoted-mention", "postgres-allows-docker"],
)
def test_non_runtime_or_allowed_launches_pass(
    inner: pytest.Pytester, body: str
) -> None:
    result = _run(inner, body)

    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line(f"*{GUARD_ERROR}*")


def test_teardown_launches_are_not_enforced(inner: pytest.Pytester) -> None:
    result = _run(
        inner,
        "import pytest, subprocess\n\n@pytest.fixture\ndef cleanup():\n    yield\n"
        "    with pytest.raises(FileNotFoundError):\n"
        f'        subprocess.run(["{MISSING}", "rm"])\n\n'
        "def test_x(cleanup):\n    pass\n",
    )

    result.assert_outcomes(passed=1)
