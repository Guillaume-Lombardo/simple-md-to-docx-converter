"""Static scan that maps tests executing a container runtime to required markers.

The default local command excludes ``requires_podman`` so that it passes on a
host without Podman. A test that executes Podman without that marker makes the
command fail with ``FileNotFoundError`` on such a host. This deterministic AST
scan finds those tests; ``tests/unit/test_container_runtime_markers.py`` runs it.

A test requires a runtime when its body, a helper it calls, or a fixture it
requests executes the runtime. Helpers are followed transitively, through
``self``/``cls`` methods and across test-tree modules, including arguments a
helper forwards to a launch (positional, keyword or ``*args`` parameters, over
any number of helper levels). Fixtures are followed through conftest, imported,
class-level, autouse, ``usefixtures`` and ``request.getfixturevalue`` requests.

Recognized process launches, resolved through module imports, aliases and
from-imports bound in the lexical scope of each call:

* ``subprocess.run``/``Popen``/``call``/``check_call``/``check_output``: the
  argument vector (positional or ``args=``), an explicit ``executable``
  (keyword or ``Popen`` position 2), and a ``shell=True`` command string;
* ``subprocess.getoutput``/``getstatusoutput``, ``os.system``, ``os.popen`` and
  ``asyncio.create_subprocess_shell`` command strings;
* ``asyncio.create_subprocess_exec``, ``os.exec*``, ``os.spawn*``,
  ``os.posix_spawn*`` and ``pty.spawn`` programs;
* ``functools.partial`` objects wrapping any of the above.

A program is recognized by basename through literals, module, local and
imported test-tree constants, ``Path``/``str``/``os.fspath``/``shutil.which``/
``os.path.join``/``Path.as_posix`` wrappers, path joins, ``or`` and conditional
alternatives, list concatenation, ``shlex.split`` and ``bash``/``sh -c``
strings. In a command string, every command position is checked after ``;``,
``&&``, ``||``, ``|`` and leading ``VAR=value``, ``env``, ``exec``,
``command``, ``sudo`` or ``nohup`` words; a shell function with a runtime's
name shadows that runtime. Launching the host-native broker counts as executing
Podman because the broker refuses to start without ``/usr/bin/podman`` and
executes it. Mentions outside a command position, such as asserted text, are
not launches. The scan is conservative where it cannot tell: an argument vector
naming a runtime is flagged even when ``executable`` names another program.

Known limits, covered by the Podman-free canonical run rather than this scan:
programs built from runtime data (configuration files, environment variables,
values returned by functions, instance or class attributes), shell scripts or
files executed by a test, dynamic dispatch (``getattr``, callables stored in
containers or lambdas), pytest hooks and indirect parametrization, third-party
process libraries, application code under ``src``, helpers outside the scanned
tree, and mocks. The one reviewed test that calls a runtime-capable harness
with its reached branch patched is listed in ``PATCHED_RUNTIME_CALLS``, which
exempts only calls to that helper from that test and only while the patch is
present.
"""

from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

BROKER = "markweave-broker"
RUNTIME_MARKERS = {
    "podman": "requires_podman",
    "docker": "requires_docker",
    BROKER: "requires_podman",
}
EXECUTABLES = frozenset({"podman", "docker"})
BROKER_ENTRY_POINTS = frozenset({"markweave.broker.process", "markweave-broker"})
SHELLS = frozenset({"bash", "sh", "dash", "zsh"})
_COMMAND_PREFIXES = frozenset({"env", "exec", "command", "sudo", "nohup"})
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SEPARATORS = re.compile(r"&&|\|\||[;|&()\n`]|\$\(")
_SHELL_FUNCTION = re.compile(
    r"(?:^|[;&|\n])\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\s*\)"
)

# How each recognized function receives its program: "argv" (argument vector),
# "program" (an executable path), or "shell" (a command string), by position.
_EXEC = "argv"
_PROGRAM = "program"
_SHELL = "shell"
_SUBPROCESS_EXEC = frozenset({"run", "Popen", "call", "check_call", "check_output"})
PROCESS_FUNCTIONS: dict[str, dict[str, tuple[str, int]]] = {
    "subprocess": {
        **dict.fromkeys(_SUBPROCESS_EXEC, (_EXEC, 0)),
        "getoutput": (_SHELL, 0),
        "getstatusoutput": (_SHELL, 0),
    },
    "asyncio": {
        "create_subprocess_exec": (_PROGRAM, 0),
        "create_subprocess_shell": (_SHELL, 0),
    },
    "asyncio.subprocess": {
        "create_subprocess_exec": (_PROGRAM, 0),
        "create_subprocess_shell": (_SHELL, 0),
    },
    "os": {
        **dict.fromkeys(
            (
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
            ),
            (_PROGRAM, 0),
        ),
        **dict.fromkeys(
            (
                "spawnl",
                "spawnle",
                "spawnlp",
                "spawnlpe",
                "spawnv",
                "spawnve",
                "spawnvp",
                "spawnvpe",
            ),
            (_PROGRAM, 1),
        ),
        "system": (_SHELL, 0),
        "popen": (_SHELL, 0),
    },
    "pty": {"spawn": (_EXEC, 0)},
}
_IMPORTABLE = frozenset({*PROCESS_FUNCTIONS, "functools"})
PASSTHROUGH_CALLS = frozenset(
    {"Path", "PurePath", "PosixPath", "PurePosixPath", "str", "fspath", "which"}
)
PATH_METHODS = frozenset(
    {"as_posix", "resolve", "absolute", "expanduser", "__fspath__"}
)
_POPEN_EXECUTABLE_POSITION = 2


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


@dataclass(frozen=True)
class PatchedCall:
    """One reviewed call to a runtime-capable helper whose reached branch is patched.

    Only calls to ``callee`` (``module.function``) made directly by the listed test
    are exempt, and only while the test patches ``patched`` on the callee's module.
    Every other launch, helper call and fixture of the test is still checked.
    """

    callee: str
    patched: str
    reason: str


PATCHED_RUNTIME_CALLS = {
    "tests/unit/e2e/test_reverse_cli_workflow.py::"
    "test_main_dispatches_structured_pptx_phase": PatchedCall(
        "tests.e2e.reverse_cli_workflow.main",
        "_exercise_structured_pptx",
        "selects the structured-pptx phase, whose only runtime branch is patched",
    ),
}

type Function = ast.FunctionDef | ast.AsyncFunctionDef
type ScopeNode = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _dotted(node: ast.expr) -> list[str] | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return list(reversed(parts))


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


def _own_nodes(root: ast.AST) -> Iterator[ast.AST]:
    """Yield descendants of ``root`` that belong to its own lexical scope."""
    stack = list(ast.iter_child_nodes(root))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            stack.extend(ast.iter_child_nodes(node))


@dataclass(frozen=True)
class _Imports:
    """Names bound by imports visible in one lexical scope."""

    modules: Mapping[str, str] = field(default_factory=dict)
    functions: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    test_modules: Mapping[str, str] = field(default_factory=dict)
    test_names: Mapping[str, tuple[str, str]] = field(default_factory=dict)

    def bind(self, own_nodes: list[ast.AST], test_modules: set[str]) -> _Imports:
        """Return these bindings overridden by imports among a scope's own nodes."""
        modules = dict(self.modules)
        functions = dict(self.functions)
        tests = dict(self.test_modules)
        names = dict(self.test_names)
        for node in own_nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = alias.asname or alias.name.partition(".")[0]
                    target = alias.name if alias.asname else bound
                    if target in _IMPORTABLE or target in test_modules:
                        mapping = tests if target in test_modules else modules
                        mapping[bound] = target
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    bound = alias.asname or alias.name
                    qualified = f"{node.module}.{alias.name}"
                    if qualified in _IMPORTABLE:
                        modules[bound] = qualified
                    elif node.module in _IMPORTABLE:
                        functions[bound] = (node.module, alias.name)
                    elif qualified in test_modules:
                        tests[bound] = qualified
                    elif node.module in test_modules:
                        names[bound] = (node.module, alias.name)
        return replace(
            self,
            modules=modules,
            functions=functions,
            test_modules=tests,
            test_names=names,
        )

    def resolve(self, function: ast.expr) -> tuple[str, str] | None:
        """Return the ``(module, name)`` of an imported library function."""
        if isinstance(function, ast.Name):
            return self.functions.get(function.id)
        parts = _dotted(function)
        if parts is None or len(parts) < 2 or parts[0] not in self.modules:
            return None
        return ".".join([self.modules[parts[0]], *parts[1:-1]]), parts[-1]


@dataclass
class _Scope:
    constants: dict[str, ast.expr]
    locals: dict[str, ast.expr] = field(default_factory=dict)

    def lookup(self, name: str) -> ast.expr | None:
        return self.locals.get(name, self.constants.get(name))


def _text(node: ast.expr, scope: _Scope, seen: frozenset[str]) -> str | None:
    """Return a statically known string, with placeholders for formatted values."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id not in seen:
        value = scope.lookup(node.id)
        return None if value is None else _text(value, scope, seen | {node.id})
    if isinstance(node, ast.JoinedStr):
        return "".join(
            part.value
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
            else "_"
            for part in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _text(node.left, scope, seen)
        right = _text(node.right, scope, seen)
        return None if left is None or right is None else left + right
    return None


def _basename(node: ast.expr, scope: _Scope, seen: frozenset[str]) -> str | None:
    """Return the basename of a statically resolvable path or program expression.

    For alternatives (``a or b``, ``a if c else b``) a runtime basename wins.
    """
    result: str | None = None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        result = PurePosixPath(node.value).name
    elif isinstance(node, ast.Name) and node.id not in seen:
        value = scope.lookup(node.id)
        if value is not None:
            result = _basename(value, scope, seen | {node.id})
    elif isinstance(node, ast.BoolOp | ast.IfExp):
        options = (
            node.values if isinstance(node, ast.BoolOp) else [node.body, node.orelse]
        )
        names = [_basename(option, scope, seen) for option in options]
        result = next((name for name in names if name in EXECUTABLES), None) or next(
            (name for name in names if name), None
        )
    elif isinstance(node, ast.Call):
        result = _call_basename(node, scope, seen)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        result = _basename(node.right, scope, seen)
    return result


def _call_basename(node: ast.Call, scope: _Scope, seen: frozenset[str]) -> str | None:
    name = _call_name(node.func)
    if name in PASSTHROUGH_CALLS and node.args:
        return _basename(node.args[0], scope, seen)
    if name == "join" and node.args:
        return _basename(node.args[-1], scope, seen)
    if name in PATH_METHODS and isinstance(node.func, ast.Attribute):
        return _basename(node.func.value, scope, seen)
    return None


def _command_runtimes(command: str) -> set[str]:
    """Return runtimes started at any command position of a shell string.

    A shell function with a runtime's name shadows that runtime in the script.
    """
    shadowed = set(_SHELL_FUNCTION.findall(command))
    runtimes: set[str] = set()
    for segment in _SEPARATORS.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            words = segment.split()
        while words and (_ASSIGNMENT.match(words[0]) or words[0] in _COMMAND_PREFIXES):
            words.pop(0)
        if not words:
            continue
        program = PurePosixPath(words[0]).name
        if program in EXECUTABLES and words[0] not in shadowed:
            runtimes.add(program)
        if BROKER_ENTRY_POINTS & {PurePosixPath(word).name for word in words}:
            runtimes.add(BROKER)
        if program in SHELLS and "-c" in words[:-1]:
            nested = _command_runtimes(words[words.index("-c") + 1])
            runtimes |= nested - shadowed
    return runtimes


def _shell_runtimes(node: ast.expr, scope: _Scope) -> set[str]:
    text = _text(node, scope, frozenset())
    if text is not None:
        return _command_runtimes(text)
    if isinstance(node, ast.List | ast.Tuple) and node.elts:
        return _shell_runtimes(node.elts[0], scope)
    return set()


def _argv_runtimes(node: ast.expr, scope: _Scope, seen: frozenset[str]) -> set[str]:
    """Return the runtimes executed by a program or argument vector expression."""
    if isinstance(node, ast.Name) and node.id not in seen:
        value = scope.lookup(node.id)
        if value is not None:
            return _argv_runtimes(value, scope, seen | {node.id})
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _argv_runtimes(node.left, scope, seen)
    if isinstance(node, ast.Call) and _call_name(node.func) == "split" and node.args:
        return _shell_runtimes(node.args[0], scope)
    if not isinstance(node, ast.List | ast.Tuple):
        name = _basename(node, scope, seen)
        return {name} if name in EXECUTABLES else set()
    return _vector_runtimes(node, scope, seen)


def _vector_runtimes(
    node: ast.List | ast.Tuple, scope: _Scope, seen: frozenset[str]
) -> set[str]:
    """Return the runtimes executed by a literal argument vector."""
    if not node.elts:
        return set()
    head = node.elts[0]
    if isinstance(head, ast.Starred):
        return _argv_runtimes(head.value, scope, seen)
    runtimes: set[str] = set()
    names = [
        None if isinstance(element, ast.Starred) else _basename(element, scope, seen)
        for element in node.elts
    ]
    if names[0] in EXECUTABLES:
        runtimes.add(names[0])
    # The host-native broker may be launched through an interpreter or systemd-run.
    if BROKER_ENTRY_POINTS & set(names):
        runtimes.add(BROKER)
    if names[0] in SHELLS and "-c" in names[:-1]:
        script = node.elts[names.index("-c") + 1]
        runtimes |= _shell_runtimes(script, scope)
    return runtimes


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def _launch_inputs(
    call: ast.Call, imports: _Imports
) -> list[tuple[str, ast.expr]] | None:
    """Return the ``(style, expression)`` inputs that select a launched program.

    ``None`` means the call does not create a process.
    """
    target = imports.resolve(call.func)
    if target == ("functools", "partial") and call.args:
        wrapped = ast.Call(
            func=call.args[0], args=call.args[1:], keywords=call.keywords
        )
        return _launch_inputs(wrapped, imports)
    module, name = target or ("", "")
    style, position = PROCESS_FUNCTIONS.get(module, {}).get(name, ("", -1))
    if not style:
        return None
    program = call.args[position] if len(call.args) > position else None
    if program is None and module == "subprocess":
        program = _keyword(call, "args")
    inputs: list[tuple[str, ast.expr]] = []
    shell = _keyword(call, "shell")
    if style == _SHELL or (isinstance(shell, ast.Constant) and shell.value is True):
        if program is not None:
            inputs.append((_SHELL, program))
    elif name.startswith("create_subprocess"):
        inputs.append((_EXEC, ast.Tuple(elts=list(call.args), ctx=ast.Load())))
    elif program is not None:
        inputs.append((_EXEC if style == _EXEC else _PROGRAM, program))
    executable = _keyword(call, "executable")
    if (
        executable is None
        and name == "Popen"
        and len(call.args) > _POPEN_EXECUTABLE_POSITION
    ):
        executable = call.args[_POPEN_EXECUTABLE_POSITION]
    if executable is not None:
        inputs.append((_PROGRAM, executable))
    return inputs


def _input_runtimes(style: str, expression: ast.expr, scope: _Scope) -> set[str]:
    """Return the runtimes that one launch input selects."""
    if style == _SHELL:
        return _shell_runtimes(expression, scope)
    if style == _PROGRAM:
        name = _basename(expression, scope, frozenset())
        return {name} if name in EXECUTABLES else set()
    return _argv_runtimes(expression, scope, frozenset())


@dataclass(frozen=True)
class _Signature:
    positional: tuple[str, ...]
    vararg: str | None
    keyword_only: tuple[str, ...]


def _signature(function: Function) -> _Signature:
    arguments = function.args
    return _Signature(
        tuple(argument.arg for argument in (*arguments.posonlyargs, *arguments.args)),
        arguments.vararg.arg if arguments.vararg else None,
        tuple(argument.arg for argument in arguments.kwonlyargs),
    )


_VARARGS = "*"


def _forwarded(
    style: str, expression: ast.expr, signature: _Signature, scope: _Scope
) -> tuple[str, str] | None:
    """Return ``(parameter, style)`` when a launch input comes from a parameter."""
    parameters = {*signature.positional, *signature.keyword_only}

    def parameter(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name) and node.id not in scope.locals:
            if node.id == signature.vararg:
                return _VARARGS
            if node.id in parameters:
                return node.id
        return None

    direct = parameter(expression)
    if direct is not None:
        return direct, style
    if isinstance(expression, ast.List | ast.Tuple) and expression.elts:
        head = expression.elts[0]
        if isinstance(head, ast.Starred):
            name = parameter(head.value)
            return (name, _EXEC) if name else None
        name = parameter(head)
        return (name, _PROGRAM) if name else None
    return None


def _arguments(
    call: ast.Call, signature: _Signature, bound_method: bool
) -> dict[str, ast.expr]:
    """Map a call's statically known arguments to the callee's parameters."""
    positional = list(signature.positional)
    if bound_method and positional and positional[0] in {"self", "cls"}:
        positional.pop(0)
    mapped: dict[str, ast.expr] = {}
    for index, argument in enumerate(call.args):
        if isinstance(argument, ast.Starred):
            break
        if index < len(positional):
            mapped[positional[index]] = argument
    extra = call.args[len(positional) :]
    if signature.vararg and extra:
        mapped[_VARARGS] = ast.Tuple(elts=list(extra), ctx=ast.Load())
    for keyword in call.keywords:
        if keyword.arg is not None:
            mapped[keyword.arg] = keyword.value
    return mapped


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
    scopes: dict[int, _ScopeInfo]
    functions: dict[str, Function]
    runs: dict[str, set[str]]
    fixtures: dict[str, Function]
    autouse: set[str]


def _module_name(path: Path, relative_to: Path) -> str:
    parts = list(path.relative_to(relative_to).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


@dataclass(frozen=True)
class _ScopeInfo:
    """Imports, calls and nested scopes that belong to one lexical scope."""

    imports: _Imports
    calls: list[ast.Call]
    children: list[ast.AST]


def _scope_infos(tree: ast.Module, test_modules: set[str]) -> dict[int, _ScopeInfo]:
    """Analyse each module, class and function scope once."""
    infos: dict[int, _ScopeInfo] = {}
    stack: list[tuple[ast.AST, _Imports]] = [(tree, _Imports())]
    while stack:
        scope, parent = stack.pop()
        own = list(_own_nodes(scope))
        imports = parent.bind(
            [node for node in own if isinstance(node, ast.Import | ast.ImportFrom)],
            test_modules,
        )
        children: list[ast.AST] = [
            node
            for node in own
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        ]
        calls = [node for node in own if isinstance(node, ast.Call)]
        infos[id(scope)] = _ScopeInfo(imports, calls, children)
        stack.extend((child, imports) for child in children)
    return infos


def _parse(path: Path, relative_to: Path, test_modules: set[str]) -> _Module:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    functions: dict[str, Function] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.setdefault(node.name, node)
    fixtures: dict[str, Function] = {}
    autouse: set[str] = set()
    candidates = [
        *tree.body,
        *(
            member
            for statement in tree.body
            if isinstance(statement, ast.ClassDef)
            for member in statement.body
        ),
    ]
    for statement in candidates:
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef):
            is_fixture, is_autouse = _fixture_options(statement)
            if is_fixture:
                fixtures.setdefault(statement.name, statement)
                if is_autouse and statement in tree.body:
                    autouse.add(statement.name)
    scopes = _scope_infos(tree, test_modules)
    return _Module(
        path,
        _module_name(path, relative_to),
        tree,
        _assignments(tree.body),
        scopes[id(tree)].imports,
        scopes,
        functions,
        {name: set() for name in functions},
        fixtures,
        autouse,
    )


def _scoped_calls(
    module: _Module, root: ScopeNode
) -> Iterator[tuple[ast.Call, _Imports]]:
    """Yield each call under ``root`` with the imports of its lexical scope."""
    stack: list[ast.AST] = [root]
    while stack:
        info = module.scopes[id(stack.pop())]
        for call in info.calls:
            yield call, info.imports
        stack.extend(info.children)


@dataclass
class _Summary:
    launched: set[str]
    callees: list[tuple[str, str]]
    forwards: dict[str, str]


class _Analysis:
    def __init__(self, modules: dict[str, _Module]) -> None:
        self.modules = modules
        self._summaries: dict[int, _Summary] = {}
        self._in_progress: set[int] = set()

    def callee(
        self, module: _Module, call: ast.Call, imports: _Imports, scope: _Scope
    ) -> tuple[str, str] | None:
        """Return the ``(module, function)`` identity of a test-tree helper call."""
        function = call.func
        if isinstance(function, ast.Attribute) and isinstance(function.value, ast.Name):
            if function.value.id in {"self", "cls"}:
                return module.name, function.attr
            owner = imports.test_modules.get(function.value.id)
            return (owner, function.attr) if owner else None
        if not isinstance(function, ast.Name):
            return None
        name = function.id
        alias = scope.locals.get(name)
        if isinstance(alias, ast.Name):
            name = alias.id
        if name in module.runs:
            return module.name, name
        return imports.test_names.get(name)

    def summary(self, module: _Module, function: Function) -> _Summary:
        """Return a function's launches, helper calls and forwarded parameters."""
        cached = self._summaries.get(id(function))
        if cached is not None:
            return cached
        if id(function) in self._in_progress:
            return _Summary(set(), [], {})
        self._in_progress.add(id(function))
        scope = _Scope(module.constants, _assignments(list(ast.walk(function))))
        scope.constants = {**self.imported_constants(module), **scope.constants}
        signature = _signature(function)
        summary = _Summary(set(), [], {})
        for call, imports in _scoped_calls(module, function):
            inputs = _launch_inputs(call, imports)
            if inputs is None:
                callee = self.callee(module, call, imports, scope)
                if callee is not None:
                    summary.callees.append(callee)
                    inputs = self.forwarded_inputs(callee, call)
            for style, expression in inputs or ():
                summary.launched |= _input_runtimes(style, expression, scope)
                forwarded = _forwarded(style, expression, signature, scope)
                if forwarded is not None:
                    summary.forwards.setdefault(*forwarded)
        self._in_progress.discard(id(function))
        self._summaries[id(function)] = summary
        return summary

    def forwarded_inputs(
        self, callee: tuple[str, str], call: ast.Call
    ) -> list[tuple[str, ast.expr]]:
        """Return a helper's launch inputs supplied by this call's arguments."""
        owner = self.modules.get(callee[0])
        function = owner.functions.get(callee[1]) if owner else None
        if owner is None or function is None:
            return []
        forwards = self.summary(owner, function).forwards
        if not forwards:
            return []
        bound = isinstance(call.func, ast.Attribute)
        arguments = _arguments(call, _signature(function), bound)
        return [
            (style, arguments[parameter])
            for parameter, style in forwards.items()
            if parameter in arguments
        ]

    def imported_constants(self, module: _Module) -> dict[str, ast.expr]:
        """Return module-level constants imported from other test-tree modules."""
        constants: dict[str, ast.expr] = {}
        for bound, (source, name) in module.imports.test_names.items():
            owner = self.modules.get(source)
            if owner is not None and name in owner.constants:
                constants[bound] = owner.constants[name]
        return constants

    def direct(
        self,
        module: _Module,
        function: Function,
        exempt_callees: frozenset[tuple[str, str]] = frozenset(),
    ) -> set[str]:
        summary = self.summary(module, function)
        found = set(summary.launched)
        for owner_name, name in summary.callees:
            owner = self.modules.get(owner_name)
            if owner is not None and (owner_name, name) not in exempt_callees:
                found |= owner.runs.get(name, set())
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
            for requested in (
                _parameters(fixture)
                | _usefixtures(fixture.decorator_list)
                | _dynamic_fixtures(fixture)
            ):
                found |= self.fixture_runs(requested, visible, seen | {name})
            return found
        return set()

    def patch_present(
        self, module: _Module, test: Function, entry: PatchedCall
    ) -> bool:
        """Return whether the test patches ``entry.patched`` on the callee module."""
        callee_module = entry.callee.rpartition(".")[0]
        for call, imports in _scoped_calls(module, test):
            name = _call_name(call.func)
            arguments = [
                argument.value if isinstance(argument, ast.Constant) else argument
                for argument in call.args
            ]
            if name == "patch" and arguments[:1] == [
                f"{callee_module}.{entry.patched}"
            ]:
                return True
            if (
                name in {"object", "setattr"}
                and len(arguments) > 1
                and arguments[1] == entry.patched
                and isinstance(call.args[0], ast.Name)
                and imports.test_modules.get(call.args[0].id) == callee_module
            ):
                return True
        return False


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


def _dynamic_fixtures(function: Function) -> set[str]:
    """Return fixtures requested through ``request.getfixturevalue("name")``."""
    return {
        node.args[0].value
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and _call_name(node.func) == "getfixturevalue"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def scan(
    tests_root: Path,
    relative_to: Path,
    broker_exemptions: Mapping[str, BrokerExemption] = BROKER_LAUNCHES_WITHOUT_PODMAN,
    patched_calls: Mapping[str, PatchedCall] = PATCHED_RUNTIME_CALLS,
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
            test_id = f"{relative}::{test.name}"
            exempt: frozenset[tuple[str, str]] = frozenset()
            entry = patched_calls.get(test_id)
            if entry is not None and analysis.patch_present(
                module, test.function, entry
            ):
                callee_module, _, callee = entry.callee.rpartition(".")
                exempt = frozenset({(callee_module, callee)})
                if analysis.direct(module, test.function) != analysis.direct(
                    module, test.function, exempt
                ):
                    result.patched.add(test_id)
            requested = (
                _parameters(test.function)
                | _usefixtures(test.function.decorator_list)
                | _dynamic_fixtures(test.function)
                | test.fixtures
                | autouse
            )
            runtimes = analysis.direct(module, test.function, exempt)
            for fixture in requested:
                runtimes |= analysis.fixture_runs(fixture, visible, frozenset())
            if not runtimes:
                continue
            result.requirements.append(Requirement(test_id, frozenset(runtimes)))
            _check_markers(test, test_id, runtimes, broker_exemptions, result)
    return result
