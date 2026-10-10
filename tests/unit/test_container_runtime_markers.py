"""Guard that every test executing a container runtime carries its marker."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.container_runtime_markers import (
    BROKER,
    BROKER_LAUNCHES_WITHOUT_PODMAN,
    PATCHED_RUNTIME_CALLS,
    BrokerExemption,
    scan,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
BROKER_TESTS = "tests/integration/broker/test_broker_process_integration.py"


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _violations(
    root: Path, exemptions: dict[str, BrokerExemption] | None = None
) -> list[str]:
    result = scan(root, root, exemptions or {}, {})
    return [str(item) for item in result.violations]


def test_every_repository_container_runtime_invocation_is_marked() -> None:
    result = scan(ROOT / "tests", ROOT)
    assert [str(violation) for violation in result.violations] == []
    # Every documented broker exemption still names a test and cases that exist.
    assert result.exempted == set(BROKER_LAUNCHES_WITHOUT_PODMAN)
    assert result.unknown_exempt_cases == set()
    assert result.patched == set(PATCHED_RUNTIME_CALLS)
    podman_tests = {
        requirement.test_id
        for requirement in result.requirements
        if {"podman", BROKER} & requirement.runtimes
    }
    # Keep the guard honest: it must still recognize the known Podman suites.
    assert {
        "tests/integration/container/test_registry_transport.py::"
        "test_registry_stage_digest_matches_exact_manifest_bytes",
        "tests/integration/broker/test_podman_runtime_integration.py::"
        "test_real_rootless_podman_whole_unit_lifecycle_and_proof",
        f"{BROKER_TESTS}::test_real_process_serves_complete_lifecycle_over_mtls",
        f"{BROKER_TESTS}::"
        "test_real_process_rejects_wrong_mtls_pin_without_inventory_mutation",
    } <= podman_tests


def test_guard_fails_when_the_malformed_key_case_loses_its_marker(
    tmp_path: Path,
) -> None:
    source = (ROOT / BROKER_TESTS).read_text(encoding="utf-8")
    marked = 'pytest.param("malformed", marks=pytest.mark.requires_podman)'
    assert source.count(marked) == 1
    _write(tmp_path, BROKER_TESTS, source.replace(marked, '"malformed"'))

    result = scan(tmp_path / "tests", tmp_path)

    assert [str(violation) for violation in result.violations] == [
        f"{BROKER_TESTS}::test_real_process_rejects_insecure_mtls_key_before_state"
        "_mutation[malformed] lacks pytest.mark.requires_podman"
    ]


@pytest.mark.parametrize(
    "source",
    [
        'import subprocess\n\ndef test_x():\n    subprocess.run(["podman", "ps"])\n',
        'import subprocess\nPODMAN = "/usr/bin/podman"\n\n'
        'def test_x():\n    subprocess.run((PODMAN, "ps"), check=True)\n',
        "import subprocess\nfrom pathlib import Path\n"
        'PODMAN = Path("/usr/bin/podman")\n\n'
        "def _podman(*arguments):\n"
        "    return subprocess.run([str(PODMAN), *arguments])\n\n"
        'def _cleanup():\n    _podman("rm")\n\n'
        "def test_x():\n    _cleanup()\n",
        "import subprocess\n\ndef test_x():\n"
        '    command = ["/usr/bin/podman", "info"]\n    subprocess.Popen(command)\n',
        "import asyncio\n\nasync def test_x():\n"
        '    await asyncio.create_subprocess_exec("podman", "ps")\n',
        "import subprocess\n\ndef test_x():\n"
        '    subprocess.check_output(args=["podman", "ps"])\n',
        'import subprocess\n\ndef _podman():\n    subprocess.run(["podman"])\n\n'
        "def test_x():\n    real = _podman\n    real()\n",
        'from subprocess import run\n\ndef test_x():\n    run(["/usr/bin/podman", "ps"])\n',
        'import subprocess as sp\n\ndef test_x():\n    sp.run(["podman", "ps"])\n',
        'from subprocess import Popen as P\n\ndef test_x():\n    P(["podman", "ps"])\n',
        "import asyncio as aio\n\nasync def test_x():\n"
        '    await aio.create_subprocess_exec("podman", "ps")\n',
        "from asyncio import create_subprocess_exec as spawn\n\n"
        'async def test_x():\n    await spawn("podman", "ps")\n',
        "from asyncio import subprocess as asp\n\n"
        'async def test_x():\n    await asp.create_subprocess_exec("podman")\n',
        "import os\n\ndef test_x():\n"
        '    os.execv("/usr/bin/podman", ["podman", "ps"])\n',
        "from os import spawnv\nimport os\n\ndef test_x():\n"
        '    spawnv(os.P_WAIT, "/usr/bin/podman", ["podman", "ps"])\n',
        "from os import posix_spawn as ps\n\ndef test_x():\n"
        '    ps("/usr/bin/podman", ["podman"], {})\n',
        "import shutil, subprocess\n\ndef test_x():\n"
        '    podman = shutil.which("podman")\n    subprocess.run([podman, "ps"])\n',
        "def test_x():\n    import subprocess as local\n"
        '    local.call(["podman", "ps"])\n',
    ],
    ids=[
        "literal",
        "constant",
        "transitive-helper",
        "local-argv",
        "asyncio",
        "args-keyword",
        "local-alias",
        "from-import",
        "module-alias",
        "from-import-alias",
        "asyncio-module-alias",
        "asyncio-from-import-alias",
        "asyncio-subprocess-module",
        "os-exec",
        "os-spawn-from-import",
        "os-posix-spawn-alias",
        "which-variable",
        "function-local-import",
    ],
)
def test_guard_detects_unmarked_podman_invocations(tmp_path: Path, source: str) -> None:
    _write(tmp_path, "test_sample.py", source)
    assert _violations(tmp_path) == [
        "test_sample.py::test_x lacks pytest.mark.requires_podman"
    ]


@pytest.mark.parametrize(
    "importer",
    [
        "from helpers.runtime import podman_ps\n\ndef test_x():\n    podman_ps()\n",
        "import helpers.runtime as runtime\n\ndef test_x():\n    runtime.podman_ps()\n",
        "from helpers import runtime\n\ndef test_x():\n    runtime.podman_ps()\n",
        "from helpers.runtime import image\n\ndef test_x(image):\n    pass\n",
    ],
    ids=["helper-from-import", "module-alias", "module-from-import", "fixture"],
)
def test_guard_follows_helpers_and_fixtures_in_other_modules(
    tmp_path: Path, importer: str
) -> None:
    _write(tmp_path, "helpers/__init__.py", "")
    _write(
        tmp_path,
        "helpers/runtime.py",
        "import pytest\nfrom subprocess import run\n\n"
        'def podman_ps():\n    run(["podman", "ps"])\n\n'
        "@pytest.fixture\ndef image():\n    podman_ps()\n",
    )
    _write(tmp_path, "test_sample.py", importer)
    assert _violations(tmp_path) == [
        "test_sample.py::test_x lacks pytest.mark.requires_podman"
    ]


def test_guard_detects_fixture_chains_conftest_and_autouse(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "conftest.py",
        "import pytest, subprocess\n\n"
        "@pytest.fixture\ndef image():\n"
        '    subprocess.run(["podman", "build"])\n    yield "image"\n',
    )
    _write(
        tmp_path,
        "nested/test_sample.py",
        "import pytest, subprocess\n\n"
        "@pytest.fixture\ndef container(image):\n    yield image\n\n"
        "def test_chain(container):\n    pass\n\n"
        '@pytest.mark.usefixtures("image")\ndef test_usefixtures():\n    pass\n\n'
        "class TestGroup:\n    def test_method(self, image):\n        pass\n",
    )
    _write(
        tmp_path,
        "test_autouse.py",
        "import pytest, subprocess\n\n"
        "@pytest.fixture(autouse=True)\ndef runtime():\n"
        '    subprocess.run(["docker", "info"])\n\n'
        "def test_any():\n    pass\n",
    )
    assert _violations(tmp_path) == [
        "nested/test_sample.py::test_chain lacks pytest.mark.requires_podman",
        "nested/test_sample.py::test_usefixtures lacks pytest.mark.requires_podman",
        "nested/test_sample.py::TestGroup::test_method lacks "
        "pytest.mark.requires_podman",
        "test_autouse.py::test_any lacks pytest.mark.requires_docker",
    ]


@pytest.mark.parametrize(
    "source",
    [
        "import pytest, subprocess\n\n@pytest.mark.requires_podman\n"
        'def test_x():\n    subprocess.run(["podman", "ps"])\n',
        "import pytest, subprocess\npytestmark = [pytest.mark.integration, "
        "pytest.mark.requires_podman]\n\n"
        'def test_x():\n    subprocess.run(["podman", "ps"])\n',
        "import pytest, subprocess\npytestmark = pytest.mark.requires_podman\n\n"
        'def test_x():\n    subprocess.run(["podman", "ps"])\n',
        "import pytest, subprocess\n\n@pytest.mark.requires_podman\n"
        "class TestX:\n    def test_x(self):\n"
        '        subprocess.run(["podman", "ps"])\n',
        "import pytest, subprocess\n\n@pytest.mark.parametrize(\n"
        '    "name", [pytest.param("a", marks=pytest.mark.requires_podman), '
        'pytest.param("b", marks=[pytest.mark.requires_podman])]\n)\n'
        'def test_x(name):\n    subprocess.run(["podman", name])\n',
        # Mentions in asserted text, fake executables and other programs are not
        # Podman invocations; neither is a local function that shadows run.
        "import subprocess\n\ndef test_x(tmp_path):\n"
        '    fake = tmp_path / "podman"\n'
        '    assert "podman run --rm" in "podman run --rm image"\n'
        '    subprocess.run(["bash", "script.sh"], env={"RUNTIME": "podman"})\n',
        'def run(argv):\n    return argv\n\ndef test_x():\n    run(["echo"])\n',
    ],
    ids=[
        "function",
        "module-list",
        "module-single",
        "class",
        "every-case",
        "not-an-invocation",
        "unrelated-run",
    ],
)
def test_guard_accepts_marked_or_non_invoking_tests(
    tmp_path: Path, source: str
) -> None:
    _write(tmp_path, "test_sample.py", source)
    assert _violations(tmp_path) == []


def test_guard_requires_every_case_when_marks_are_per_parameter(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import pytest, subprocess\n\n@pytest.mark.parametrize(\n"
        '    "name", [pytest.param("a", marks=pytest.mark.requires_podman), "b"]\n)\n'
        'def test_x(name):\n    subprocess.run(["podman", name])\n',
    )
    assert _violations(tmp_path) == [
        "test_sample.py::test_x lacks pytest.mark.requires_podman"
    ]


def test_guard_requires_each_runtime_marker_separately(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import pytest, subprocess\n\n@pytest.mark.requires_podman\n"
        "def test_x():\n"
        '    subprocess.run(["podman", "ps"])\n'
        '    subprocess.run(["docker", "compose", "config"])\n',
    )
    assert _violations(tmp_path) == [
        "test_sample.py::test_x lacks pytest.mark.requires_docker"
    ]


@pytest.mark.parametrize(
    "launch",
    [
        '(sys.executable, "-m", "markweave.broker.process", str(config))',
        '("/usr/bin/systemd-run", "--user", str(BROKER), str(config))',
    ],
    ids=["interpreter", "systemd-run"],
)
def test_guard_treats_broker_launches_as_podman_unless_exempted(
    tmp_path: Path, launch: str
) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        "import subprocess, sys\nfrom pathlib import Path\n"
        'BROKER = Path("/repo") / ".venv/bin/markweave-broker"\n\n'
        f"def _start(config):\n    return subprocess.Popen({launch})\n\n"
        'def test_x():\n    _start("broker.json")\n',
    )
    assert _violations(tmp_path) == [
        "test_sample.py::test_x lacks pytest.mark.requires_podman"
    ]
    exempted = scan(
        tmp_path, tmp_path, {"test_sample.py::test_x": BrokerExemption("reason")}, {}
    )
    assert exempted.violations == []
    assert exempted.exempted == {"test_sample.py::test_x"}


def test_broker_case_exemptions_require_marks_on_every_other_case(
    tmp_path: Path,
) -> None:
    template = (
        "import pytest, subprocess, sys\n\n@pytest.mark.parametrize(\n"
        '    "failure", [{malformed}, "mode", pytest.param("x", id="other")]\n)\n'
        "def test_x(failure):\n"
        '    subprocess.run((sys.executable, "-m", "markweave.broker.process"))\n'
    )
    exemptions = {
        "test_sample.py::test_x": BrokerExemption(
            "reason", frozenset({"mode", "missing"})
        )
    }
    _write(tmp_path, "test_sample.py", template.format(malformed='"malformed"'))
    result = scan(tmp_path, tmp_path, exemptions, {})
    assert [str(item) for item in result.violations] == [
        "test_sample.py::test_x[malformed] lacks pytest.mark.requires_podman",
        "test_sample.py::test_x[other] lacks pytest.mark.requires_podman",
    ]
    assert result.unknown_exempt_cases == {"test_sample.py::test_x[missing]"}

    marked = 'pytest.param("malformed", marks=pytest.mark.requires_podman)'
    _write(
        tmp_path,
        "test_sample.py",
        template.format(malformed=marked).replace(
            'pytest.param("x", id="other")',
            'pytest.param("x", id="other", marks=pytest.mark.requires_podman)',
        ),
    )
    assert scan(tmp_path, tmp_path, exemptions, {}).violations == []


def test_patched_runtime_calls_are_skipped_only_when_listed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "test_sample.py",
        'import subprocess\n\ndef _main():\n    subprocess.run(["podman"])\n\n'
        "def test_x(mocker):\n    _main()\n",
    )
    assert _violations(tmp_path) == [
        "test_sample.py::test_x lacks pytest.mark.requires_podman"
    ]
    result = scan(tmp_path, tmp_path, {}, {"test_sample.py::test_x": "patched"})
    assert result.violations == []
    assert result.patched == {"test_sample.py::test_x"}
