"""Verify that every final-image E2E scenario runs in exactly one shard.

`scripts/e2e/run.sh` groups its scenarios into `e2e_phase_*` functions and
assigns every phase to one `e2e_shard_<n>_phases` array. A scenario is any
`run_browser_test` or `run_reverse_lifecycle` call and any
`uv run python -m tests.e2e.*` workflow invocation. This module parses the
harness and fails when a scenario is outside every phase, reachable from more
than one shard, reachable from unsharded top-level code, or hidden in a seed.
Seeds (`e2e_seed_*`) may only run `seed-*` workflow commands, and opt-in
diagnostics (`e2e_diagnostic_*`) are excluded because they never qualify a run.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

RUN_SCRIPT = Path("scripts/e2e/run.sh")
PHASE_PREFIX = "e2e_phase_"
SEED_PREFIX = "e2e_seed_"
DIAGNOSTIC_PREFIX = "e2e_diagnostic_"
PRIMITIVES = frozenset({"run_browser_test", "run_reverse_lifecycle"})
FUNCTION_START = re.compile(r"^([a-z_][a-z0-9_]*)\(\) \{$")
SHARD_ARRAY = re.compile(
    r"^readonly -a e2e_shard_([1-9][0-9]*)_phases=\(([a-z0-9 -]*)\)$"
)
SCENARIO_CALL = re.compile(
    r"(?:^|[\s;!&|(])(?P<primitive>run_browser_test|run_reverse_lifecycle)(?:\s|$)"
    r"|uv run python -m (?P<module>tests\.e2e\.[a-z_]+)"
)
DISPATCHER = '"e2e_phase_${phase//-/_}"'


@dataclass(frozen=True)
class Scenario:
    """One scenario invocation site in the harness."""

    line: int
    function: str | None
    label: str


@dataclass(frozen=True)
class Harness:
    """The parsed structure of the final-image E2E harness."""

    functions: Mapping[str, tuple[int, int]]
    lines: tuple[str, ...]
    shards: Mapping[int, tuple[str, ...]]


def _phase_name(function: str) -> str:
    return function.removeprefix(PHASE_PREFIX).replace("_", "-")


def parse_harness(text: str) -> tuple[Harness, list[str]]:
    """Return the function spans and shard arrays of a harness script."""
    lines = tuple(text.splitlines())
    functions: dict[str, tuple[int, int]] = {}
    shards: dict[int, tuple[str, ...]] = {}
    errors: list[str] = []
    current: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        if current is None:
            if (match := FUNCTION_START.fullmatch(line)) is not None:
                name = match.group(1)
                if name in functions:
                    errors.append(f"function {name} is defined more than once")
                current = (name, index)
            elif (match := SHARD_ARRAY.fullmatch(line)) is not None:
                shard = int(match.group(1))
                if shard in shards:
                    errors.append(f"shard {shard} is declared more than once")
                shards[shard] = tuple(match.group(2).split())
        elif line == "}":
            functions[current[0]] = (current[1], index)
            current = None
    if current is not None:
        errors.append(f"function {current[0]} is not terminated")
    return Harness(functions=functions, lines=lines, shards=shards), errors


def _owner(harness: Harness, index: int) -> str | None:
    for name, (start, end) in harness.functions.items():
        if start < index < end:
            return name
    return None


def _logical_command(lines: Sequence[str], index: int) -> str:
    parts = [lines[index].strip()]
    while parts[-1].endswith("\\") and index + 1 < len(lines):
        index += 1
        parts[-1] = parts[-1].removesuffix("\\").strip()
        parts.append(lines[index].strip())
    return " ".join(parts)


def _label(command: str, match: re.Match[str]) -> str:
    if (module := match.group("module")) is not None:
        words = command[match.end() :].split()
        detail = [
            word
            for position, word in enumerate(words)
            if (position == 0 and not word.startswith("-"))
            or (position > 0 and words[position - 1] == "--phase")
        ]
        return " ".join([module.removeprefix("tests.e2e."), *detail])
    if match.group("primitive") == "run_reverse_lifecycle":
        words = command[match.end() :].split()
        return f"reverse lifecycle {words[0] if words else '?'}"
    test = re.search(r"/e2e/(browser-[a-z0-9-]+)\.(?:test\.)?mjs", command)
    variants = re.findall(
        r"MARKWEAVE_E2E_(?:COMPOSER_PHASE|REVERSE_PHASE|RUNTIME_FAILURE)=([a-z-]+)",
        command,
    )
    return " ".join([test.group(1) if test else "browser ?", *variants])


def scenarios(harness: Harness) -> list[Scenario]:
    """Return every scenario site outside the primitive definitions."""
    found: list[Scenario] = []
    for index, line in enumerate(harness.lines):
        stripped = line.strip()
        if stripped.startswith("#") or FUNCTION_START.fullmatch(line):
            continue
        owner = _owner(harness, index)
        if owner in PRIMITIVES:
            continue
        command = _logical_command(harness.lines, index)
        # The logical command starts with this stripped line, so its first
        # matches are exactly this line's scenario calls.
        count = len(SCENARIO_CALL.findall(line))
        found.extend(
            Scenario(line=index + 1, function=owner, label=_label(command, match))
            for match in list(SCENARIO_CALL.finditer(command))[:count]
        )
    return found


def _calls(harness: Harness) -> dict[str | None, set[str]]:
    names = sorted(harness.functions, key=len, reverse=True)
    pattern = re.compile(
        r"(?<![\w-])(" + "|".join(map(re.escape, names)) + r")(?![\w-])"
    )
    calls: dict[str | None, set[str]] = {}
    for index, line in enumerate(harness.lines):
        if line.strip().startswith("#") or FUNCTION_START.fullmatch(line):
            continue
        owner = _owner(harness, index)
        for match in pattern.finditer(line):
            if match.group(1) != owner:
                calls.setdefault(owner, set()).add(match.group(1))
    return calls


def _reach(calls: Mapping[str | None, set[str]], start: str | None) -> set[str]:
    reached: set[str] = set()
    pending = list(calls.get(start, ()))
    while pending:
        name = pending.pop()
        if name in reached or name in PRIMITIVES:
            continue
        reached.add(name)
        pending.extend(calls.get(name, ()))
    return reached


def _validate_shards(harness: Harness, expected_count: int | None) -> list[str]:
    errors: list[str] = []
    numbers = sorted(harness.shards)
    if numbers != list(range(1, len(numbers) + 1)) or len(numbers) < 2:  # noqa: PLR2004
        errors.append("the harness must declare at least two contiguous shards")
    if expected_count is not None and len(numbers) != expected_count:
        errors.append(
            f"the harness declares {len(numbers)} shards but CI runs {expected_count}"
        )
    phases = {
        _phase_name(name) for name in harness.functions if name.startswith(PHASE_PREFIX)
    }
    listed = [phase for shard in numbers for phase in harness.shards[shard]]
    errors.extend(
        f"phase {phase} is assigned to more than one shard"
        for phase in sorted({phase for phase in listed if listed.count(phase) > 1})
    )
    errors.extend(
        f"phase {phase} is assigned to no shard"
        for phase in sorted(phases.difference(listed))
    )
    errors.extend(
        f"shard phase {phase} has no e2e_phase function"
        for phase in sorted(set(listed).difference(phases))
    )
    text = "\n".join(harness.lines)
    for shard in numbers:
        selection = (
            f'  {shard}) selected_phases=("${{e2e_shard_{shard}_phases[@]}}") ;;'
        )
        if selection not in harness.lines:
            errors.append(f"shard {shard} is not selectable by MARKWEAVE_E2E_SHARD")
    complete = " ".join(f'"${{e2e_shard_{shard}_phases[@]}}"' for shard in numbers)
    if f"  all) selected_phases=({complete}) ;;" not in harness.lines:
        errors.append("the complete run must execute every shard in order")
    if text.count(DISPATCHER) != 1:
        errors.append("phases must run only through the selected-phase dispatcher")
    return errors


def validate_harness(text: str, *, expected_count: int | None = None) -> list[str]:
    """Return every shard-assignment error in a harness script."""
    harness, errors = parse_harness(text)
    errors.extend(_validate_shards(harness, expected_count))
    calls = _calls(harness)
    phase_functions = [
        name for name in harness.functions if name.startswith(PHASE_PREFIX)
    ]
    for name in phase_functions:
        if any(name in called for called in calls.values()):
            errors.append(f"phase function {name} is called outside the dispatcher")
    shard_of = {
        phase: shard for shard, phases in harness.shards.items() for phase in phases
    }
    reach = {name: {name, *_reach(calls, name)} for name in phase_functions}
    top_level = _reach(calls, None)
    for scenario in scenarios(harness):
        function = scenario.function
        where = f"line {scenario.line} ({scenario.label})"
        if function is not None and function.startswith(DIAGNOSTIC_PREFIX):
            continue
        if function is not None and function.startswith(SEED_PREFIX):
            words = scenario.label.split()
            if len(words) < 2 or not words[1].startswith("seed-"):  # noqa: PLR2004
                errors.append(f"seed {function} runs a scenario at {where}")
            continue
        if function is None or function in top_level:
            errors.append(f"scenario at {where} runs outside the shard phases")
            continue
        reaching = {
            shard_of[_phase_name(phase)]
            for phase, functions in reach.items()
            if function in functions and _phase_name(phase) in shard_of
        }
        if not reaching:
            errors.append(f"scenario at {where} is assigned to no shard")
        elif len(reaching) > 1:
            errors.append(
                f"scenario at {where} runs in shards {sorted(reaching)}, not exactly one"
            )
    for name in harness.functions:
        if name.startswith(SEED_PREFIX) and name in top_level:
            errors.append(f"seed {name} runs outside the shard phases")
    return errors


def shard_map(text: str) -> dict[int, list[tuple[str, str]]]:
    """Return each shard's phases and scenario labels in harness order."""
    harness, _errors = parse_harness(text)
    calls = _calls(harness)
    found = scenarios(harness)
    result: dict[int, list[tuple[str, str]]] = {}
    for shard, phases in sorted(harness.shards.items()):
        for phase in phases:
            function = PHASE_PREFIX + phase.replace("-", "_")
            members = {function, *_reach(calls, function)}
            result.setdefault(shard, []).extend(
                (phase, scenario.label)
                for scenario in found
                if scenario.function in members
                and not str(scenario.function).startswith(
                    (SEED_PREFIX, DIAGNOSTIC_PREFIX)
                )
            )
    return result


def validate_files(paths: Iterable[Path], *, expected_count: int) -> list[str]:
    """Validate committed harness files against the CI shard count."""
    errors: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            errors.append(f"{path}: cannot read the E2E harness: {error}")
            continue
        errors.extend(
            f"{path}: {error}"
            for error in validate_harness(text, expected_count=expected_count)
        )
    return errors


def main(arguments: Sequence[str] | None = None) -> int:
    """Print the shard map and fail on any assignment error."""
    from scripts.ci.select_domains import E2E_SHARD_COUNT  # noqa: PLC0415

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--script", type=Path, default=RUN_SCRIPT)
    options = parser.parse_args(arguments)
    text = options.script.read_text(encoding="utf-8")
    for shard, entries in shard_map(text).items():
        for phase, label in entries:
            print(f"shard {shard} | {phase} | {label}")
    errors = validate_harness(text, expected_count=E2E_SHARD_COUNT)
    for error in errors:
        print(f"error: {error}")
    return bool(errors)


if __name__ == "__main__":  # pragma: no cover - command-line entry point
    raise SystemExit(main())
