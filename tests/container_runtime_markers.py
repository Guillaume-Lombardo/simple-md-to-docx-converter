"""Static scan that maps tests executing a container runtime to required markers.

The default local command excludes ``requires_podman`` so that it passes on a
host without Podman. A test that executes Podman without that marker makes the
command fail with ``FileNotFoundError`` on such a host. This deterministic AST
scan finds those tests; ``tests/unit/test_container_runtime_markers.py`` runs it.

A test requires a runtime when its body, a helper it calls (transitively, also
across test-tree modules), or a fixture it requests (transitively, including
conftest, imported and autouse fixtures) passes the runtime executable as the
program of a subprocess call. Subprocess functions are recognized through
module imports, module aliases and from-imports of ``subprocess``, ``asyncio``
and ``os``. The program is recognized by basename through literals, module or
local variables, ``Path``/``str``/``os.fspath``/``shutil.which`` wrappers and
path joins. Launching the host-native broker counts as executing Podman because
the broker refuses to start without ``/usr/bin/podman`` and executes it.
Mentions inside larger strings, such as asserted shell fragments, are not
invocations. Podman executed by shell scripts or application code is out of the
scan's reach; the Podman-free canonical run covers that.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

BROKER = "markweave-broker"
RUNTIME_MARKERS = {
    "podman": "requires_podman",
    "docker": "requires_docker",
    BROKER: "requires_podman",
}
EXECUTABLES = frozenset({"podman", "docker"})
BROKER_ENTRY_POINTS = frozenset({"markweave.broker.process", "markweave-broker"})
_SUBPROCESS = frozenset({"run", "Popen", "call", "check_call", "check_output"})
_OS_PROGRAM_FIRST = frozenset(
    {
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "posix_spawn",
        "posix_spawnp",
    }
)
_OS_PROGRAM_SECOND = frozenset(
    {
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    }
)
PROCESS_FUNCTIONS = {
    "subprocess": _SUBPROCESS,
    "asyncio": frozenset({"create_subprocess_exec"}),
    "asyncio.subprocess": frozenset({"create_subprocess_exec"}),
    "os": _OS_PROGRAM_FIRST | _OS_PROGRAM_SECOND,
}
PASSTHROUGH_CALLS = frozenset(
    {"Path", "PurePath", "PosixPath", "PurePosixPath", "str", "fspath", "which"}
)


@dataclass(frozen=True)
class BrokerExemption:
    """A broker launch whose assertions complete before the broker reaches Podman.

    ``cases`` lists the exempt parametrized case IDs; ``None`` exempts every case.
    Every other case must carry ``requires_podman``.
    """

    reason: str
    cases: frozenset[str] | None = None


_BROKER_TESTS = "tests/integration/broker/test_broker_process_integration.py::"
BROKER_LAUNCHES_WITHOUT_PODMAN = {
    f"{_BROKER_TESTS}test_real_process_rejects_insecure_mtls_key_before_state_mutation": (
        BrokerExemption(
            "mode, symlink and FIFO key material is rejected while loading the "
            "configuration; the malformed-key case reaches the Podman check",
            frozenset({"mode", "symlink", "fifo"}),
        )
    ),
    f"{_BROKER_TESTS}test_fifo_configuration_inputs_are_rejected_without_blocking": (
        BrokerExemption(
            "FIFO configuration inputs are rejected while loading the configuration"
        )
    ),
    f"{_BROKER_TESTS}"
    "test_nonfinite_json_float_entrypoint_failure_is_bounded_and_content_free": (
        BrokerExemption("non-finite JSON is rejected while parsing the configuration")
    ),
}

# Tests that call a runtime-capable harness entry point while patching every
# runtime-reaching branch they select. Each entry is verified to still be reached.
PATCHED_RUNTIME_CALLS = {
    "tests/unit/e2e/test_reverse_cli_workflow.py::"
    "test_main_dispatches_structured_pptx_phase": (
        "selects the structured-pptx phase and patches _exercise_structured_pptx"
    ),
}

type Function = ast.FunctionDef | ast.AsyncFunctionDef


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _assignments(nodes: list[ast.stmt] | list[ast.AST]) -> dict[str, ast.expr]:
    values: dict[str, ast.expr] = {}
    for node in nodes:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    values[target.id] = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            values[node.target.id] = node.value
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
    """Return the runtimes executed by a program or argument vector expression."""
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


@dataclass
class _Imports:
    """Names bound by imports anywhere in one module."""

    process_modules: dict[str, str] = field(default_factory=dict)
    process_functions: dict[str, tuple[str, str]] = field(default_factory=dict)
    test_modules: dict[str, str] = field(default_factory=dict)
    test_names: dict[str, tuple[str, str]] = field(default_factory=dict)


def _bind_import(node: ast.Import, test_modules: set[str], found: _Imports) -> None:
    for alias in node.names:
        if alias.name in PROCESS_FUNCTIONS:
            if alias.asname is not None:
                found.process_modules[alias.asname] = alias.name
            elif "." not in alias.name:
                found.process_modules[alias.name] = alias.name
        elif alias.name in test_modules and alias.asname is not None:
            found.test_modules[alias.asname] = alias.name


def _bind_import_from(
    node: ast.ImportFrom, test_modules: set[str], found: _Imports
) -> None:
    if not node.module or node.level:
        return
    for alias in node.names:
        bound = alias.asname or alias.name
        qualified = f"{node.module}.{alias.name}"
        if qualified in PROCESS_FUNCTIONS:
            found.process_modules[bound] = qualified
        elif alias.name in PROCESS_FUNCTIONS.get(node.module, ()):
            found.process_functions[bound] = (node.module, alias.name)
        elif qualified in test_modules:
            found.test_modules[bound] = qualified
        elif node.module in test_modules:
            found.test_names[bound] = (node.module, alias.name)


def _imports(tree: ast.Module, test_modules: set[str]) -> _Imports:
    found = _Imports()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            _bind_import(node, test_modules, found)
        elif isinstance(node, ast.ImportFrom):
            _bind_import_from(node, test_modules, found)
    return found


def _process_argv(call: ast.Call, imports: _Imports) -> ast.expr | None:
    """Return the program or argument vector passed to a process-creating call."""
    function = call.func
    target: tuple[str, str] | None = None
    if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
        module = imports.process_modules.get(function.value.id)
        if module is not None and function.attr in PROCESS_FUNCTIONS[module]:
            target = (module, function.attr)
    elif isinstance(function, ast.Name):
        target = imports.process_functions.get(function.id)
    if target is None:
        return None
    module, name = target
    if name == "create_subprocess_exec":
        return ast.Tuple(elts=list(call.args), ctx=ast.Load()) if call.args else None
    if module == "os":
        index = 1 if name in _OS_PROGRAM_SECOND else 0
        return call.args[index] if len(call.args) > index else None
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg == "args":
            return keyword.value
    return None


def _decorator_name(decorator: ast.expr) -> str | None:
    """Return the dotted name of a decorator, such as ``pytest.mark.unit``."""
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    parts: list[str] = []
    while isinstance(decorator, ast.Attribute):
        parts.append(decorator.attr)
        decorator = decorator.value
    if isinstance(decorator, ast.Name):
        parts.append(decorator.id)
    return ".".join(reversed(parts)) or None


def _markers(expressions: list[ast.expr]) -> set[str]:
    names: set[str] = set()
    for expression in expressions:
        dotted = _decorator_name(expression) or ""
        if dotted.startswith("pytest.mark."):
            names.add(dotted.removeprefix("pytest.mark."))
    return names


def _mark_list(value: ast.expr) -> set[str]:
    elements = value.elts if isinstance(value, ast.List | ast.Tuple) else [value]
    return _markers(list(elements))


@dataclass(frozen=True)
class _Case:
    id: str
    marks: frozenset[str]


def _parametrize_cases(decorators: list[ast.expr]) -> list[list[_Case]]:
    """Return statically known parametrized cases, one list per decorator."""
    groups: list[list[_Case]] = []
    for decorator in decorators:
        if not (
            isinstance(decorator, ast.Call)
            and _decorator_name(decorator) == "pytest.mark.parametrize"
        ):
            continue
        keywords = {keyword.arg: keyword.value for keyword in decorator.keywords}
        values = (
            decorator.args[1] if len(decorator.args) > 1 else keywords.get("argvalues")
        )
        if not isinstance(values, ast.List | ast.Tuple):
            continue
        ids = keywords.get("ids")
        explicit = (
            [
                element.value if isinstance(element, ast.Constant) else None
                for element in ids.elts
            ]
            if isinstance(ids, ast.List | ast.Tuple)
            else []
        )
        cases: list[_Case] = []
        for index, element in enumerate(values.elts):
            marks: set[str] = set()
            value: ast.expr | None = element
            case_id: object = None
            if isinstance(element, ast.Call) and _call_name(element.func) == "param":
                for keyword in element.keywords:
                    if keyword.arg == "marks":
                        marks = _mark_list(keyword.value)
                    elif keyword.arg == "id" and isinstance(
                        keyword.value, ast.Constant
                    ):
                        case_id = keyword.value.value
                value = element.args[0] if len(element.args) == 1 else None
            if case_id is None and index < len(explicit):
                case_id = explicit[index]
            if case_id is None and isinstance(value, ast.Constant):
                case_id = value.value
            cases.append(
                _Case(
                    str(case_id) if case_id is not None else f"#{index}",
                    frozenset(marks),
                )
            )
        groups.append(cases)
    return groups


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
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs)
    }


@dataclass
class _Module:
    path: Path
    name: str
    tree: ast.Module
    constants: dict[str, ast.expr]
    imports: _Imports
    functions: dict[str, Function]
    runs: dict[str, set[str]]
    fixtures: dict[str, Function]
    autouse: set[str]


def _module_name(path: Path, relative_to: Path) -> str:
    parts = list(path.relative_to(relative_to).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _parse(path: Path, relative_to: Path, test_modules: set[str]) -> _Module:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions: dict[str, Function] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.setdefault(node.name, node)
    fixtures: dict[str, Function] = {}
    autouse: set[str] = set()
    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            is_fixture, is_autouse = _fixture_options(statement)
            if is_fixture:
                fixtures[statement.name] = statement
                if is_autouse:
                    autouse.add(statement.name)
    return _Module(
        path,
        _module_name(path, relative_to),
        tree,
        _assignments(tree.body),
        _imports(tree, test_modules),
        functions,
        {name: set() for name in functions},
        fixtures,
        autouse,
    )


class _Analysis:
    def __init__(self, modules: dict[str, _Module]) -> None:
        self.modules = modules

    def helper_runs(self, module: _Module, call: ast.Call, scope: _Scope) -> set[str]:
        """Return runtimes executed by a call to a known helper function."""
        function = call.func
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            owner = self.modules.get(
                module.imports.test_modules.get(function.value.id, "")
            )
            return set(owner.runs.get(function.attr, ())) if owner else set()
        if not isinstance(function, ast.Name):
            return set()
        name = function.id
        alias = scope.locals.get(name)
        if isinstance(alias, ast.Name):
            name = alias.id
        if name in module.runs:
            return set(module.runs[name])
        imported = module.imports.test_names.get(name)
        if imported is not None:
            owner = self.modules.get(imported[0])
            if owner is not None:
                return set(owner.runs.get(imported[1], ()))
        return set()

    def direct(self, module: _Module, function: Function) -> set[str]:
        scope = _Scope(module.constants, _assignments(list(ast.walk(function))))
        found: set[str] = set()
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            argv = _process_argv(node, module.imports)
            if argv is not None:
                found |= _argv_runtimes(argv, scope, frozenset())
            found |= self.helper_runs(module, node, scope)
        return found

    def resolve_helpers(self) -> None:
        changed = True
        while changed:
            changed = False
            for module in self.modules.values():
                for name, function in module.functions.items():
                    found = self.direct(module, function)
                    if not found <= module.runs[name]:
                        module.runs[name] |= found
                        changed = True

    def fixture(self, name: str, owner: _Module) -> tuple[_Module, Function] | None:
        if name in owner.fixtures:
            return owner, owner.fixtures[name]
        imported = owner.imports.test_names.get(name)
        if imported is not None:
            source = self.modules.get(imported[0])
            if source is not None and imported[1] in source.fixtures:
                return source, source.fixtures[imported[1]]
        return None

    def visible(self, module: _Module) -> list[_Module]:
        """Return the module, then conftest modules from nearest to farthest."""
        conftests = {
            candidate.path: candidate
            for candidate in self.modules.values()
            if candidate.path.name == "conftest.py"
        }
        visible = [module]
        for directory in module.path.parents:
            conftest = conftests.get(directory / "conftest.py")
            if conftest is not None and conftest is not module:
                visible.append(conftest)
        return visible

    def fixture_runs(
        self, name: str, visible: list[_Module], seen: frozenset[str]
    ) -> set[str]:
        if name in seen:
            return set()
        for owner in visible:
            resolved = self.fixture(name, owner)
            if resolved is None:
                continue
            source, fixture = resolved
            found = set(source.runs[fixture.name])
            for requested in _parameters(fixture) | _usefixtures(
                fixture.decorator_list
            ):
                found |= self.fixture_runs(requested, visible, seen | {name})
            return found
        return set()


@dataclass(frozen=True)
class Violation:
    """A test or parametrized case that runs a runtime without its marker."""

    test_id: str
    marker: str

    def __str__(self) -> str:
        return f"{self.test_id} lacks pytest.mark.{self.marker}"


@dataclass(frozen=True)
class Requirement:
    """A test that executes a container runtime."""

    test_id: str
    runtimes: frozenset[str]


@dataclass
class ScanResult:
    """Runtime requirements, marker violations and applied broker exemptions."""

    requirements: list[Requirement] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    exempted: set[str] = field(default_factory=set)
    patched: set[str] = field(default_factory=set)
    unknown_exempt_cases: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _Test:
    name: str
    function: Function
    markers: frozenset[str]
    fixtures: frozenset[str]


def _tests(module: _Module) -> list[_Test]:
    """Return each test with its own, class and module markers."""
    module_markers = (
        _mark_list(module.constants["pytestmark"])
        if "pytestmark" in module.constants
        else set()
    )
    collected: list[_Test] = []
    for statement in module.tree.body:
        if isinstance(
            statement, ast.FunctionDef | ast.AsyncFunctionDef
        ) and statement.name.startswith("test"):
            collected.append(
                _Test(
                    statement.name,
                    statement,
                    frozenset(module_markers | _markers(statement.decorator_list)),
                    frozenset(),
                )
            )
        elif isinstance(statement, ast.ClassDef) and statement.name.startswith("Test"):
            class_markers = module_markers | _markers(statement.decorator_list)
            class_fixtures = frozenset(_usefixtures(statement.decorator_list))
            collected.extend(
                _Test(
                    f"{statement.name}::{member.name}",
                    member,
                    frozenset(class_markers | _markers(member.decorator_list)),
                    class_fixtures,
                )
                for member in statement.body
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef)
                and member.name.startswith("test")
            )
    return collected


def _uncovered_cases(
    marker: str, groups: list[list[_Case]], exempt: frozenset[str] = frozenset()
) -> list[str] | None:
    """Return case IDs lacking ``marker``; ``None`` means the whole test lacks it."""
    if any(
        group and all(marker in case.marks or case.id in exempt for case in group)
        for group in groups
    ):
        return []
    if exempt and len(groups) == 1:
        return [
            case.id
            for case in groups[0]
            if marker not in case.marks and case.id not in exempt
        ]
    return None


def _check_markers(
    test: _Test,
    test_id: str,
    runtimes: set[str],
    broker_exemptions: Mapping[str, BrokerExemption],
    result: ScanResult,
) -> None:
    """Record violations for one test that executes container runtimes."""
    groups = _parametrize_cases(test.function.decorator_list)
    required = {RUNTIME_MARKERS[runtime] for runtime in runtimes - {BROKER}}
    exemption = broker_exemptions.get(test_id) if BROKER in runtimes else None
    if BROKER in runtimes and exemption is None:
        required.add(RUNTIME_MARKERS[BROKER])
    result.violations.extend(
        Violation(test_id, marker)
        for marker in sorted(required - test.markers)
        if _uncovered_cases(marker, groups) is None
    )
    if exemption is None:
        return
    result.exempted.add(test_id)
    marker = RUNTIME_MARKERS[BROKER]
    if exemption.cases is None or marker in required | test.markers:
        return
    known = {case.id for group in groups for case in group}
    result.unknown_exempt_cases |= {
        f"{test_id}[{case}]" for case in exemption.cases - known
    }
    missing = _uncovered_cases(marker, groups, exemption.cases)
    if missing is None:
        result.violations.append(Violation(test_id, marker))
    else:
        result.violations.extend(
            Violation(f"{test_id}[{case}]", marker) for case in missing
        )


def scan(
    tests_root: Path,
    relative_to: Path,
    broker_exemptions: Mapping[str, BrokerExemption] = BROKER_LAUNCHES_WITHOUT_PODMAN,
    patched_calls: Mapping[str, str] = PATCHED_RUNTIME_CALLS,
) -> ScanResult:
    """Scan a tests tree and return runtime requirements and marker violations."""
    paths = sorted(tests_root.rglob("*.py"))
    names = {_module_name(path, relative_to) for path in paths}
    modules = {
        module.name: module
        for module in (_parse(path, relative_to, names) for path in paths)
    }
    analysis = _Analysis(modules)
    analysis.resolve_helpers()
    result = ScanResult()
    for module in modules.values():
        if not module.path.name.startswith("test"):
            continue
        visible = analysis.visible(module)
        autouse = {name for owner in visible for name in owner.autouse}
        relative = module.path.relative_to(relative_to).as_posix()
        for test in _tests(module):
            requested = (
                _parameters(test.function)
                | _usefixtures(test.function.decorator_list)
                | test.fixtures
                | autouse
            )
            runtimes = analysis.direct(module, test.function)
            for fixture in requested:
                runtimes |= analysis.fixture_runs(fixture, visible, frozenset())
            if not runtimes:
                continue
            test_id = f"{relative}::{test.name}"
            if test_id in patched_calls:
                result.patched.add(test_id)
                continue
            result.requirements.append(Requirement(test_id, frozenset(runtimes)))
            _check_markers(test, test_id, runtimes, broker_exemptions, result)
    return result
