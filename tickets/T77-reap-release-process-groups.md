---
ticket: T77
linear_id: G1L-576
linear_url: https://linear.app/g1lom/issue/G1L-576/t77-reap-release-subprocess-groups-reliably
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T77 - Reap release subprocess groups reliably

## Objective

Diagnose and repair release-command timeout cleanup so descendants are terminated and reaped reliably on both CI and development hosts.

## Acceptance criteria

- Reproduce the audit failure in tests/release/test_process_integration.py: process group could not be reaped instead of the expected timeout.
- Distinguish live descendants from zombies and document supported Linux reaping behavior.
- Preserve bounded deadlines, fixed argv, whole-group termination, and safe errors; do not hide cleanup failures.
- Add real-process integration regressions for timeout, descendant exit, and cleanup failure; validate the release workflow boundary with its applicable E2E tests.
- Run the canonical checks and record any unavailable dependency explicitly.

## Dependencies

- T22

## Progress

- 2026-09-19: Reproduced the audit failure under a Linux subreaper that retains orphan
  zombies; the original regression depended on how promptly the host reaped them.
- 2026-09-20: Completed through [PR #239](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/239),
  squash-merged as `d88e6c40ab95728a41b46c89ae37f3ed782a80cd`. Verified all six changed files
  against the approved source and passed 22 focused release-process tests on merged main.
- Release commands temporarily adopt orphan descendants, preserve and restore the caller's Linux
  subreaper setting, serialize that process-wide ownership, and reap only their own process group.
  Bounded reap batches preserve cleanup deadlines even with continuously exiting descendants.
  Live leftovers and genuine adoption, inspection, signalling, or reaping failures remain errors.
- Regressions cover timeout, SIGTERM-resistant descendants, live and zombie exit, cleanup failure,
  unrelated-child exit status, concurrent callers, state restoration, continuous reaping, and finite
  multi-batch cleanup. Fast real-process regressions run in the required light-coverage CI gate.
- Validation passed: `uv sync --all-groups`, Ruff formatting and lint, `uv run ty check`, and all
  216 release tests, including real builds and clean installation of every supported profile.
  Independent review approved exact source `b247948106dbbff27bc6346779e364d58e0120b3` with no
  remaining findings. Protected [CI run 35473138907](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35473138907)
  passed 3,985 tests with 93.93% total coverage and 90.08% application branch coverage.
- The portable acceptance probe passed success, timeout, live-leftover, zombie, and cleanup-failure
  cases in rootless final image `d85ed005e46a589e4c8767dadaf18fced34af61c1ef0a04989e70aea9ce862ee`
  (source label `1586bedd7375b54c4799dc686dc04d3af88bea5b`), with read-only source/root,
  UID 10042:0, no network or capabilities, 64 PIDs, and 256 MiB. This verifies host release tooling
  on the final-image Linux/Python runtime; the tooling is not shipped in the application image.
- Interrupted broad local runs were not counted as passes. The orchestrator reconciled them with
  T78's canonical baseline (4,315 passing tests; the known T77 regression and broker coordination
  failures separately resolved), clean broker reruns, scoped release tests, and protected CI.
  Full `uv run pytest` remains unverified locally because Pandoc, Mermaid CLI, and LibreOffice
  are unavailable. No application, storage-profile, package-version, or image behavior changed.
- The local and remote implementation branches were deleted after verified merge. Linear was
  marked Done and re-fetched after main verification; this record synchronizes the repository mirror.
- Integrated validation: the final T79 candidate incorporating this repair and T78 passed all 4,339 canonical default tests, with 45 engine-marked tests deselected, 94.89% overall coverage, and 91.22% application branch coverage. This clean run resolves the earlier broad-run uncertainty.

## Synchronization

Keep Linear and this mirror synchronized. Mark Done only after verification on main.
