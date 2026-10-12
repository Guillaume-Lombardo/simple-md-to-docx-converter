---
ticket: T98
linear_id: G1L-630
linear_url: https://linear.app/g1lom/issue/G1L-630/t98-make-the-local-python-test-suite-fast-enough-for-iteration
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T98 - Make the local Python test suite fast enough for iteration

## Objective

Reduce the wall time of the unit and canonical local suites so a contributor can run them routinely; a plain `pytest tests/unit` run did not finish within 15 minutes during the 2026-10-09 analysis.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: follow-up note on test duration).

## Acceptance criteria

- Measure and record the baseline with `--durations` for unit and canonical runs.
- Add parallel execution (for example pytest-xdist, through `uv`) that is compatible with the branch-coverage plugin, disposable PostgreSQL/RustFS services and mutation testing; keep results deterministic.
- Move or mark genuinely slow tests with `slow` without excluding them from CI.
- Document a fast developer loop and keep the canonical commands authoritative.
- Target: unit suite under 3 minutes and canonical engine-excluding suite under 10 minutes on the reference developer host; record the achieved figures.
- Coverage thresholds (90% branch, 90% changed lines) and CI gates stay unchanged.

## Dependencies

- T97 - Mark Podman-dependent tests and repair the default local test command

## Blocking points

- Coverage merging across workers and the custom `scripts.ci.pytest_branch_coverage` plugin must be proven equivalent before CI adopts parallelism. Recommended approach (owner-approved 2026-10-09): pytest-xdist with pytest-cov's built-in xdist support, after a short spike on the custom plugin.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

- 2026-10-10: Started a narrowly scoped CI reliability correction during T96 PR #278
  qualification. Attempt 1 passed all 2,701 shard tests in 29:59 before cancellation prevented
  coverage upload; attempt 2 reached 99% without failures before the same 30-minute cutoff.
  Raise only the reviewed Python shard bound to 40 minutes with matching validator and regression
  checks. Preserve test selection, two complementary shards, same-attempt coverage, 90% gates,
  permissions and all other job bounds. The broader measurement, parallelism and developer-loop
  criteria remain pending behind T97; this correction does not complete T98.

- 2026-10-10: The bounded CI correction passed exact-head CI 38008305925 and was verified
  on main in PR #278 squash `628fd2f310a456c6e2b94b25c071f321cbef1d6b`. Both complementary
  Python shards and distributed coverage were combined from the same attempt; 5,106 tests passed
  with 90.14% application branch coverage and unchanged 90% gates. The reviewed 40-minute bound
  remains finite; no selection, permission or other job-limit change was introduced. This is
  partial delivery only: duration measurement, parallelism, developer-loop documentation and
  reference-host speed targets remain pending behind T97. T98 remains In Progress.

- 2026-10-12: Root cause and fix (PR #285, rebased onto main `f31640b`). Almost all Python test
  time was the autouse per-test `gc.collect()` in `tests/conftest.py`: about 1,600 objects stayed
  reachable per test (FastAPI's three module-level dependency `lru_cache` tables pin every
  test-built app, engine and metadata object), so every collection traversed a growing heap and
  suite time grew quadratically. The one-time freeze after collection did not fix it at scale.
  The harness now runs `gc.collect()` then `gc.freeze()` at every test teardown, clears the three
  FastAPI caches first (guard test fails if FastAPI renames them), and runs a session-finish
  backstop (`gc.unfreeze(); gc.collect()`) after the last fixture teardown that fails the session
  on an unclosed resource; xdist workers forward backstop findings to the controller. Harness
  tests prove, serially and under xdist, that a cyclic leak still fails the test that created it,
  that module- and session-scoped leaks fail the session through the backstop, and that a clean
  session passes; mutation checks confirmed each test fails without its mechanism.
- 2026-10-12: Six parametrized tests built node IDs from `uuid4()` values or ZIP timestamps, so
  xdist workers collected different tests and `--lf` could not match failures. They now use fixed
  identifiers or explicit `ids`; a regression test collects the suite in two processes and
  compares node IDs.
- 2026-10-12: Local service stacks (`markweave-tests-<id>`) leaked by interrupted runs (one ran
  for 28 hours, five stayed exited for 39 hours). Stacks, volumes and networks now carry owner
  lock, creation-time and host labels; the owner holds an exclusive `flock` for the stack's life.
  `SIGTERM` becomes `KeyboardInterrupt` while a stack is owned, `SIGINT`/`SIGTERM` are deferred
  until `compose down` finishes, removal runs in its own session, and an interpreter-exit net
  remains. Each acquisition, and the xdist controller after a worker crash, removes labelled
  stacks on the same host whose lock nobody holds; a stack whose lock file is gone is removed only
  after `MARKWEAVE_TEST_SERVICES_STALE_AFTER_SECONDS` (default 3600, provisional). Live stacks and
  unlabelled projects are never touched. Unit tests cover the lifecycle and reaper decisions;
  Docker integration tests cover `SIGTERM`, `SIGINT`, `SIGKILL` plus reaping next to a live owner,
  and an xdist worker crash.
- 2026-10-12: Measurements on the reference developer host (4 vCPU VM; other worktrees' E2E stacks
  were running concurrently, so figures are conservative). Baseline: CI light shards took 28-39
  minutes (0.68-0.78 s per test); the canonical local default command took about 65 minutes;
  `--durations` on a bounded subset (`tests/unit/broker`, `tests/unit/conversion`,
  `tests/unit/reversions`, 2,018 tests) took 279 s on main. After: the same subset 15.4 s;
  `tests/unit` (4,027 tests) 75 s serial without coverage, 84 s with coverage, 47 s with
  `-n auto`; CI selection `-m "unit or light_coverage"` (5,658 tests, CI coverage flags) 315 s
  serial and 164 s with `-n auto`; canonical default command 407 s serial (5,930 passed, gates
  met) and 162 s with `-n auto --dist loadgroup` (5,930 passed). Targets met: unit under 3 minutes
  and canonical under 10 minutes. Peak RSS of the serial CI selection without coverage fell from
  1,024 MB to 776 MB with the FastAPI cache clearing.
- 2026-10-12: Coverage. Canonical run: 90.45% branches serial and 90.49% with xdist, total 94.35%
  and 94.36%. CI selection plus the distributed-coverage selection, combined as the
  `python-coverage` job does: 90.43% serial and 90.47% parallel application branches; changed
  application lines 100% (no application code changed). The only per-file differences came from
  two broker unit tests that failed in the serial run while another worktree's host broker held
  the per-user broker authority lock, plus a few timing-dependent branches. Thresholds and CI
  gates are unchanged.
- 2026-10-12: Remaining work. Adopt `-n auto --dist loadgroup` in the CI Python shard commands
  and lower the shard `timeout-minutes` from 40 to about 15 in `.github/workflows/ci.yml`,
  `scripts/ci/validate_ci.py` and `tests/test_ci_validation.py` after PR #297 (T130) merges.
  Known parallel risks to settle with that change: the timing-based
  `test_failed_authentication_medians_have_no_legacy_or_malformed_fast_path` failed once under
  `-n auto` on the loaded host, and the `tests/unit/broker/test_process.py` factory tests take
  the real per-user broker authority lock, so they fail while any host broker runs.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-630.
