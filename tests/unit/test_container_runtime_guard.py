"""Tests for the runtime guard that requires markers on container runtime launches."""

from __future__ import annotations

import pytest

from tests.container_runtime_guard import (
    ContainerRuntimeGuard,
    launched_runtimes,
    text_runtimes,
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
        ("x=$(docker ps)", {"docker"}),
        (f'echo "$({MISSING} ps)"', {"podman"}),
        (f"echo `{MISSING} ps`", {"podman"}),
        ("echo 'ok; podman ps'", {"podman"}),
        ("echo podman", {"podman"}),
        ("/opt/fake-bin/podman/", {"podman"}),
        ("podman-compose up", set()),
        ("cat compose.podman.yaml /opt/podman.log", set()),
        ("echo docker.io/library/postgres", set()),
        ("echo mypodman podman_x run-podman.sh", set()),
    ],
)
def test_runtime_names_count_as_shell_words_anywhere(
    command: str, expected: set[str]
) -> None:
    assert text_runtimes(command) == expected


def _popen(*argv: str, executable: str | None = None) -> tuple[object, ...]:
    return (executable or argv[0], list(argv), None, None)


@pytest.mark.parametrize(
    ("event", "arguments", "expected"),
    [
        ("subprocess.Popen", _popen("podman", "ps"), {"podman"}),
        ("subprocess.Popen", _popen(MISSING, "ps"), {"podman"}),
        ("subprocess.Popen", _popen("echo", executable="/usr/bin/podman"), {"podman"}),
        # Conservative: any argument naming a runtime counts, whatever runs it.
        ("subprocess.Popen", _popen("podman", "x", executable="/bin/echo"), {"podman"}),
        ("subprocess.Popen", _popen("echo", "podman"), {"podman"}),
        ("subprocess.Popen", _popen("bash", "-ec", MISSING), {"podman"}),
        ("subprocess.Popen", _popen("sh", "-xc", "podman ps"), {"podman"}),
        ("subprocess.Popen", _popen("env", "-u", "T97_DUMMY", MISSING), {"podman"}),
        ("subprocess.Popen", _popen("env", "-S", "podman ps"), {"podman"}),
        ("subprocess.Popen", _popen("nice", "-n", "5", "podman"), {"podman"}),
        ("subprocess.Popen", _popen("timeout", "5", "docker", "ps"), {"docker"}),
        ("subprocess.Popen", _popen("xargs", "podman", "rm"), {"podman"}),
        (
            "subprocess.Popen",
            (b"/usr/bin/podman", [b"/usr/bin/podman"], None, None),
            {"podman"},
        ),
        ("subprocess.Popen", ("echo hi", "echo hi", None, None), set()),
        ("subprocess.Popen", _popen("bash", "scripts/run-podman.sh"), set()),
        ("subprocess.Popen", _popen("echo", "ok"), set()),
        ("os.system", (b"podman ps",), {"podman"}),
        ("os.system", ("echo ok",), set()),
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
        "import subprocess\n\ndef test_x():\n"
        f"    subprocess.run('echo \"$({MISSING} ps)\"', shell=True)\n",
        f'import os\n\ndef test_x():\n    os.system("echo `{MISSING} ps`")\n',
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
        "command-substitution",
        "backticks",
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
        'import subprocess\n\ndef test_x():\n    subprocess.run(["echo", "ok"])\n',
        "import pytest, subprocess\n\n"
        '@pytest.mark.container_runtime_guard_exempt(reason="echo only prints it")\n'
        'def test_x():\n    subprocess.run(["echo", "podman"])\n',
        "import pytest, subprocess\n\n@pytest.mark.requires_postgres\ndef test_x():\n"
        "    with pytest.raises(FileNotFoundError):\n"
        '        subprocess.run(["/nonexistent/bin/docker"])\n',
    ],
    ids=["no-runtime", "exempt-mention", "postgres-allows-docker"],
)
def test_non_runtime_or_allowed_launches_pass(
    inner: pytest.Pytester, body: str
) -> None:
    result = _run(inner, body)

    result.assert_outcomes(passed=1)
    result.stdout.no_fnmatch_line(f"*{GUARD_ERROR}*")


def test_shell_mentions_are_conservatively_flagged(inner: pytest.Pytester) -> None:
    result = _run(
        inner,
        "import os\n\ndef test_x():\n    os.system(\"echo 'ok; podman ps' >/dev/null\")\n",
    )

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([f"*{GUARD_ERROR}*launched podman*"])


def test_unmarked_spawn_fails_through_the_forked_child(
    inner: pytest.Pytester,
) -> None:
    result = _run(
        inner,
        "import os\n\ndef test_x():\n"
        f'    status = os.spawnv(os.P_WAIT, "{MISSING}", ["podman", "ps"])\n'
        "    assert status == 127\n",
    )

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        ["*Container runtime launched without its marker:*", "*launched podman*"]
    )


def test_marked_spawn_is_not_blocked(inner: pytest.Pytester) -> None:
    result = _run(
        inner,
        "import os, pytest\n\n@pytest.mark.requires_podman\ndef test_x():\n"
        f'    assert os.spawnv(os.P_WAIT, "{MISSING}", ["podman"]) == 127\n',
    )

    result.assert_outcomes(passed=1)


@pytest.mark.parametrize(
    "body",
    [
        "import pytest, subprocess\n\ndef test_x():\n    try:\n"
        f'        subprocess.run(["{MISSING}"])\n'
        "    except Exception:\n        pytest.skip('hidden')\n",
        "import pytest, subprocess\n\n@pytest.mark.xfail\ndef test_x():\n"
        f'    subprocess.run(["{MISSING}"])\n',
        "import pytest, subprocess\n\n@pytest.mark.xfail(strict=True)\ndef test_x():\n"
        f'    subprocess.run(["{MISSING}"])\n',
        "import pytest, subprocess\n\n@pytest.mark.xfail(strict=False)\ndef test_x():\n"
        "    try:\n"
        f'        subprocess.run(["{MISSING}"])\n'
        "    except Exception:\n        pass\n",
    ],
    ids=["skip-after-catch", "xfail", "xfail-strict", "xpass-after-catch"],
)
def test_violation_fails_despite_skip_or_xfail(
    inner: pytest.Pytester, body: str
) -> None:
    result = _run(inner, body)

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*Container runtime launched without its marker:*"])
    assert result.ret == pytest.ExitCode.TESTS_FAILED


def test_teardown_launches_are_not_enforced(inner: pytest.Pytester) -> None:
    result = _run(
        inner,
        "import pytest, subprocess\n\n@pytest.fixture\ndef cleanup():\n    yield\n"
        "    with pytest.raises(FileNotFoundError):\n"
        f'        subprocess.run(["{MISSING}", "rm"])\n\n'
        "def test_x(cleanup):\n    pass\n",
    )

    result.assert_outcomes(passed=1)


@pytest.mark.parametrize(
    "argv",
    [
        f'["bash", "-ec", "{MISSING}"]',
        f'["sh", "-xc", "{MISSING} ps"]',
        f'["env", "-u", "T97_DUMMY", "{MISSING}"]',
        f'["env", "-S", "{MISSING} ps"]',
        f'["nice", "-n", "5", "{MISSING}"]',
        f'["timeout", "5", "{MISSING}"]',
        f'["xargs", "{MISSING}"]',
        '["podman", "hello"], executable="/bin/echo"',
    ],
    ids=[
        "bash-ec",
        "sh-xc",
        "env-u",
        "env-S",
        "nice",
        "timeout",
        "xargs",
        "explicit-executable",
    ],
)
def test_launcher_forms_are_flagged_before_they_start(
    inner: pytest.Pytester, argv: str
) -> None:
    result = _run(
        inner, f"import subprocess\n\ndef test_x():\n    subprocess.run({argv})\n"
    )

    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [f"*{GUARD_ERROR}*launched podman without*container_runtime_guard_exempt*"]
    )


@pytest.mark.parametrize(
    "marker",
    [
        "@pytest.mark.container_runtime_guard_exempt\n",
        '@pytest.mark.container_runtime_guard_exempt(reason="  ")\n',
    ],
    ids=["missing-reason", "blank-reason"],
)
def test_exempt_marker_requires_a_reason(inner: pytest.Pytester, marker: str) -> None:
    result = _run(
        inner,
        f"import pytest, subprocess\n\n{marker}"
        'def test_x():\n    subprocess.run(["echo", "podman"])\n',
    )

    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(
        ["*container_runtime_guard_exempt requires a non-empty*"]
    )


def test_exempt_marker_suppresses_only_the_guard(inner: pytest.Pytester) -> None:
    result = _run(
        inner,
        "import pytest, subprocess\n\n"
        '@pytest.mark.container_runtime_guard_exempt(reason="checks the error path")\n'
        "def test_x():\n"
        f'    subprocess.run(["{MISSING}"])\n',
    )

    # The test is still selected and run; only the guard is silent.
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines([f"*FileNotFoundError*{NOT_FOUND}*"])
    result.stdout.no_fnmatch_line(f"*{GUARD_ERROR}*")
