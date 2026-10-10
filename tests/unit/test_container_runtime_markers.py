"""Guard that every test executing a container runtime carries its marker.

The default local command excludes ``requires_podman`` so that it passes on a
host without Podman. A test that executes the Podman executable without that
marker would make the command fail with ``FileNotFoundError`` on such a host.
This deterministic static scan finds those tests before they reach a host.

A test requires a runtime when its body, a module helper it calls (transitively),
or a fixture it requests (transitively, including autouse fixtures) passes the
runtime executable as the first argument vector element of a subprocess call.
The executable is recognized by basename through literals, module or local
constants, ``Path``/``str``/``os.fspath``/``shutil.which`` wrappers, and path
joins. Mentions inside larger strings, such as asserted shell script fragments,
are not invocations.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
BROKER = "markweave-broker"
# The host-native broker refuses to start without /usr/bin/podman and executes it.
RUNTIME_MARKERS = {
    "podman": "requires_podman",
    "docker": "requires_docker",
    BROKER: "requires_podman",
}
EXECUTABLES = frozenset({"podman", "docker"})
BROKER_ENTRY_POINTS = frozenset({"markweave.broker.process", "markweave-broker"})
# Broker launches whose assertions complete in configuration loading, before the
# broker checks for or executes Podman. Each entry is verified to still exist.
BROKER_LAUNCHES_WITHOUT_PODMAN = {
    "tests/integration/broker/test_broker_process_integration.py::"
    "test_real_process_rejects_insecure_mtls_key_before_state_mutation": (
        "mode, symlink and FIFO key material is rejected while loading the "
        "configuration; the malformed-key case reaches the runtime check and "
        "carries requires_podman on its parameter"
    ),
    "tests/integration/broker/test_broker_process_integration.py::"
    "test_fifo_configuration_inputs_are_rejected_without_blocking": (
        "FIFO configuration inputs are rejected while loading the configuration"
    ),
    "tests/integration/broker/test_broker_process_integration.py::"
    "test_nonfinite_json_float_entrypoint_failure_is_bounded_and_content_free": (
        "non-finite JSON is rejected while parsing the configuration"
    ),
}
SUBPROCESS_MODULES = frozenset({"subprocess", "asyncio", "os"})
SUBPROCESS_FUNCTIONS = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
        "create_subprocess_exec",
        "execv",
        "execve",
        "execvp",
        "execvpe",
    }
)
PASSTHROUGH_CALLS = frozenset(
    {"Path", "PurePath", "PosixPath", "str", "fspath", "which"}
)

type Function = ast.FunctionDef | ast.AsyncFunctionDef


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _assignments(body: list[ast.stmt]) -> dict[str, ast.expr]:
    values: dict[str, ast.expr] = {}
    for statement in body:
        if isinstance(statement, ast.Assign):
            for target in statement.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = statement.value
        elif (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.value is not None
        ):
            values[statement.target.id] = statement.value
    return values


def _local_assignments(function: Function) -> dict[str, ast.expr]:
    values: dict[str, ast.expr] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign | ast.AnnAssign):
            values.update(_assignments([node]))
    return values


@dataclass
class _Scope:
    constants: dict[str, ast.expr]
    locals: dict[str, ast.expr] = field(default_factory=dict)

    def lookup(self, name: str) -> ast.expr | None:
        return self.locals.get(name, self.constants.get(name))


def _basename(node: ast.expr, scope: _Scope, seen: frozenset[str]) -> str | None:
    """Return the basename of a statically resolvable path or program expression."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return PurePosixPath(node.value).name
    if isinstance(node, ast.Name) and node.id not in seen:
        value = scope.lookup(node.id)
        return None if value is None else _basename(value, scope, seen | {node.id})
    if isinstance(node, ast.Call) and node.args:
        if _call_name(node.func) in PASSTHROUGH_CALLS:
            return _basename(node.args[0], scope, seen)
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _basename(node.right, scope, seen)
    return None


def _argv_runtimes(node: ast.expr, scope: _Scope, seen: frozenset[str]) -> set[str]:
    """Return the runtimes executed by a subprocess argument vector."""
    if isinstance(node, ast.Name) and node.id not in seen:
        value = scope.lookup(node.id)
        if value is not None:
            return _argv_runtimes(value, scope, seen | {node.id})
    if not isinstance(node, ast.List | ast.Tuple):
        name = _basename(node, scope, seen)
        return {name} if name in EXECUTABLES else set()
    if not node.elts:
        return set()
    head = node.elts[0]
    if isinstance(head, ast.Starred):
        return _argv_runtimes(head.value, scope, seen)
    runtimes: set[str] = set()
    name = _basename(head, scope, seen)
    if name in EXECUTABLES:
        runtimes.add(name)
    # The host-native broker may be launched through an interpreter or systemd-run.
    for element in node.elts:
        if not isinstance(element, ast.Starred) and (
            _basename(element, scope, seen) in BROKER_ENTRY_POINTS
        ):
            runtimes.add(BROKER)
    return runtimes


def _subprocess_argv(call: ast.Call) -> ast.expr | None:
    function = call.func
    if not (
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id in SUBPROCESS_MODULES
        and function.attr in SUBPROCESS_FUNCTIONS
    ):
        return None
    if function.attr == "create_subprocess_exec" and call.args:
        return ast.Tuple(elts=list(call.args), ctx=ast.Load())
    if function.value.id == "os" and call.args:
        return call.args[0]
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg == "args":
            return keyword.value
    return None


def _decorator_name(decorator: ast.expr) -> str | None:
    """Return the dotted tail of a decorator, such as ``mark.unit`` or ``fixture``."""
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    parts: list[str] = []
    while isinstance(decorator, ast.Attribute):
        parts.append(decorator.attr)
        decorator = decorator.value
    if isinstance(decorator, ast.Name):
        parts.append(decorator.id)
    return ".".join(reversed(parts)) or None


def _markers(decorators: list[ast.expr]) -> set[str]:
    names: set[str] = set()
    for decorator in decorators:
        dotted = _decorator_name(decorator) or ""
        if dotted.startswith("pytest.mark."):
            names.add(dotted.removeprefix("pytest.mark."))
    return names


def _module_markers(module: ast.Module) -> set[str]:
    value = _assignments(module.body).get("pytestmark")
    if value is None:
        return set()
    elements = value.elts if isinstance(value, ast.List | ast.Tuple) else [value]
    return _markers(list(elements))


def _fixture_options(function: Function) -> tuple[bool, bool]:
    """Return whether a function is a fixture and whether it is autouse."""
    for decorator in function.decorator_list:
        if (_decorator_name(decorator) or "").endswith("fixture"):
            autouse = isinstance(decorator, ast.Call) and any(
                keyword.arg == "autouse"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in decorator.keywords
            )
            return True, autouse
    return False, False


def _usefixtures(decorators: list[ast.expr]) -> set[str]:
    names: set[str] = set()
    for decorator in decorators:
        if (
            isinstance(decorator, ast.Call)
            and _decorator_name(decorator) == "pytest.mark.usefixtures"
        ):
            names.update(
                argument.value
                for argument in decorator.args
                if isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
            )
    return names


def _parameters(function: Function) -> set[str]:
    arguments = function.args
    return {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        )
    }


@dataclass
class _Module:
    path: Path
    tree: ast.Module
    functions: dict[str, Function]
    runs: dict[str, set[str]]
    fixtures: dict[str, Function]
    autouse: set[str]


def _direct_runtimes(
    function: Function, constants: dict[str, ast.expr], helpers: dict[str, set[str]]
) -> set[str]:
    scope = _Scope(constants, _local_assignments(function))
    found: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        argv = _subprocess_argv(node)
        if argv is not None:
            found |= _argv_runtimes(argv, scope, frozenset())
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name is not None:
            target = scope.locals.get(name)
            if isinstance(target, ast.Name):
                name = target.id
            found |= helpers.get(name, set())
    return found


def _analyse_module(path: Path) -> _Module:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants = _assignments(tree.body)
    functions: dict[str, Function] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.setdefault(node.name, node)
    runs: dict[str, set[str]] = {name: set() for name in functions}
    changed = True
    while changed:
        changed = False
        for name, function in functions.items():
            found = _direct_runtimes(function, constants, runs)
            if not found <= runs[name]:
                runs[name] |= found
                changed = True
    fixtures: dict[str, Function] = {}
    autouse: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            is_fixture, is_autouse = _fixture_options(statement)
            if is_fixture:
                fixtures[statement.name] = statement
                if is_autouse:
                    autouse.add(statement.name)
    return _Module(path, tree, functions, runs, fixtures, autouse)


@dataclass(frozen=True)
class Violation:
    """A test that executes a runtime without the matching marker."""

    path: str
    test: str
    marker: str

    def __str__(self) -> str:
        return f"{self.path}::{self.test} lacks pytest.mark.{self.marker}"


@dataclass(frozen=True)
class Requirement:
    """A test that executes a container runtime."""

    path: str
    test: str
    runtimes: frozenset[str]


def _visible_fixture_modules(
    module: _Module, conftests: dict[Path, _Module]
) -> list[_Module]:
    """Return the module, then conftest modules from nearest to farthest."""
    visible = [module]
    for directory in module.path.parents:
        conftest = conftests.get(directory / "conftest.py")
        if conftest is not None and conftest is not module:
            visible.append(conftest)
    return visible


def _fixture_runtimes(
    name: str, visible: list[_Module], seen: frozenset[str]
) -> set[str]:
    if name in seen:
        return set()
    for owner in visible:
        fixture = owner.fixtures.get(name)
        if fixture is None:
            continue
        found = set(owner.runs[name])
        for parameter in _parameters(fixture) | _usefixtures(fixture.decorator_list):
            found |= _fixture_runtimes(parameter, visible, seen | {name})
        return found
    return set()


def _tests(module: _Module) -> list[tuple[str, Function, set[str]]]:
    """Return each test with its own and its class decorator markers."""
    collected: list[tuple[str, Function, set[str]]] = []
    for statement in module.tree.body:
        if isinstance(
            statement, ast.FunctionDef | ast.AsyncFunctionDef
        ) and statement.name.startswith("test"):
            collected.append(
                (statement.name, statement, _markers(statement.decorator_list))
            )
        elif isinstance(statement, ast.ClassDef) and statement.name.startswith("Test"):
            class_markers = _markers(statement.decorator_list)
            collected.extend(
                (
                    f"{statement.name}::{member.name}",
                    member,
                    class_markers
                    | _markers(member.decorator_list)
                    | {
                        f"usefixtures:{name}"
                        for name in _usefixtures(statement.decorator_list)
                    },
                )
                for member in statement.body
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                and member.name.startswith("test")
            )
    return collected


@dataclass(frozen=True)
class ScanResult:
    """Runtime requirements, marker violations, and applied broker exemptions."""

    requirements: list[Requirement]
    violations: list[Violation]
    exempted: set[str]


def scan(
    tests_root: Path,
    relative_to: Path,
    broker_exemptions: Mapping[str, str] = BROKER_LAUNCHES_WITHOUT_PODMAN,
) -> ScanResult:
    """Scan a tests tree and return runtime requirements and marker violations."""
    modules = {path: _analyse_module(path) for path in sorted(tests_root.rglob("*.py"))}
    conftests = {
        path: module for path, module in modules.items() if path.name == "conftest.py"
    }
    result = ScanResult([], [], set())
    for path, module in modules.items():
        if not path.name.startswith("test"):
            continue
        visible = _visible_fixture_modules(module, conftests)
        autouse = {name for owner in visible for name in owner.autouse}
        module_markers = _module_markers(module.tree)
        constants = _assignments(module.tree.body)
        relative = path.relative_to(relative_to).as_posix()
        for test_name, function, markers in _tests(module):
            requested = (
                _parameters(function)
                | _usefixtures(function.decorator_list)
                | {
                    marker.removeprefix("usefixtures:")
                    for marker in markers
                    if marker.startswith("usefixtures:")
                }
                | autouse
            )
            runtimes = _direct_runtimes(function, constants, module.runs)
            for fixture in requested:
                runtimes |= _fixture_runtimes(fixture, visible, frozenset())
            test_id = f"{relative}::{test_name}"
            if BROKER in runtimes and test_id in broker_exemptions:
                runtimes.discard(BROKER)
                result.exempted.add(test_id)
            if not runtimes:
                continue
            result.requirements.append(
                Requirement(relative, test_name, frozenset(runtimes))
            )
            present = markers | module_markers
            result.violations.extend(
                Violation(relative, test_name, marker)
                for marker in sorted({RUNTIME_MARKERS[runtime] for runtime in runtimes})
                if marker not in present
            )
    return result


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _violations(root: Path) -> list[str]:
    return [str(violation) for violation in scan(root, root, {}).violations]


def test_every_repository_container_runtime_invocation_is_marked() -> None:
    result = scan(ROOT / "tests", ROOT)
    assert [str(violation) for violation in result.violations] == []
    # Every documented broker exemption still names a test that launches the broker.
    assert result.exempted == set(BROKER_LAUNCHES_WITHOUT_PODMAN)
    podman_tests = {
        f"{requirement.path}::{requirement.test}"
        for requirement in result.requirements
        if {"podman", BROKER} & requirement.runtimes
    }
    # Keep the guard honest: it must still recognize the known Podman suites.
    assert {
        "tests/integration/container/test_registry_transport.py::"
        "test_registry_stage_digest_matches_exact_manifest_bytes",
        "tests/integration/broker/test_podman_runtime_integration.py::"
        "test_real_rootless_podman_whole_unit_lifecycle_and_proof",
        "tests/integration/broker/test_broker_process_integration.py::"
        "test_real_process_serves_complete_lifecycle_over_mtls",
        "tests/integration/broker/test_broker_process_integration.py::"
        "test_real_process_rejects_wrong_mtls_pin_without_inventory_mutation",
    } <= podman_tests


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
    ],
    ids=[
        "literal",
        "constant",
        "transitive-helper",
        "local-argv",
        "asyncio",
        "args-keyword",
        "local-alias",
    ],
)
def test_guard_detects_unmarked_podman_invocations(tmp_path: Path, source: str) -> None:
    _write(tmp_path, "test_sample.py", source)
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
        # Mentions in asserted text, fake executables and other programs are not
        # Podman invocations.
        "import subprocess\n\ndef test_x(tmp_path):\n"
        '    fake = tmp_path / "podman"\n'
        '    assert "podman run --rm" in "podman run --rm image"\n'
        '    subprocess.run(["bash", "script.sh"], env={"RUNTIME": "podman"})\n',
    ],
    ids=["function", "module-list", "module-single", "class", "not-an-invocation"],
)
def test_guard_accepts_marked_or_non_invoking_tests(
    tmp_path: Path, source: str
) -> None:
    _write(tmp_path, "test_sample.py", source)
    assert _violations(tmp_path) == []


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
    exempted = scan(tmp_path, tmp_path, {"test_sample.py::test_x": "reason"})
    assert exempted.violations == []
    assert exempted.exempted == {"test_sample.py::test_x"}
