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
- 2026-10-12: Review round 2 (independent review of `1b602a2`, one major finding). Startup
  commands ran in their own session, so an interrupt or timeout killed only the direct Docker CLI
  while a Compose plugin child could keep creating resources after teardown had removed the
  stack and released its lock. Docker commands now run under `Popen` in a new session; an
  interrupt or timeout stops the whole process group (SIGTERM, bounded wait, SIGKILL) and waits
  until the leader is reaped and the group is gone. A group that survives is recorded as
  unsettled. A stack whose startup did not finish is removed repeatedly until the engine lists no
  resource of the project, and ownership and the lock are kept when that cannot be proven. A
  fake-plugin integration test (interrupt and timeout during `up`) proves no late resource appears
  after cleanup and fails with direct-child-only termination; a real Docker test interrupts
  `compose up` at its first resource and finds nothing left.
- 2026-10-12: Review round 3 (review of `d22521a`, one major finding). `stop_process_group`
  ran with the original handlers, so a second interrupt during the SIGTERM grace period, or an
  interrupt after a timeout, escaped before the survivor was recorded and teardown released
  ownership while a startup child lived. Now every Docker command is recorded as unsettled from
  start (start and record share one deferred block) until its group is proven gone; group
  termination, stream cleanup and bookkeeping run with SIGINT/SIGTERM deferred and re-raised
  afterwards; teardown and the exit net stop every recorded survivor again before removal, and
  the whole teardown block runs deferred. Regressions use a startup child that ignores SIGTERM
  and needs SIGKILL: a second interrupt during interrupt-driven termination and an interrupt
  during timeout-driven termination both remove the stack only after the child is gone and leave
  no late resource. The previous head fails them with removal while the child was alive.
- 2026-10-12: Review round 4 (review of `e5543f7`, two major findings). A completed command
  was forgotten although a descendant with redirected streams could still run, and the owner
  lock was always closed, which released the `flock` while a startup group could be alive or the
  stack still existed. One invariant now governs the owner lock and is enforced only in
  `release_if_settled`: the `flock` is released and the file unlinked only when no Docker command
  group the session started is alive and the stack's resources were removed; otherwise the lock
  stays held until the exit fallback proves both, or until process exit. Completed commands,
  successful or not, are forgotten only after the leader is reaped and the group is gone, and
  survivors are stopped. The exit fallback forgets a stack only once the engine lists none of
  its resources. Owners write every started group into the lock file, and the reaper waits for a
  live recorded group up to the stale bound; the residual limitation after a hard kill is
  documented. A table test drives eleven exit paths (normal, detached descendant with success
  and failure, interrupt during up, second interrupt, timeout, interrupt during the timeout stop,
  interrupt before the teardown, settle failure, removal failure then exit fallback, and a group
  that survives every stop) and asserts the invariant at the single release point; mutants for
  each half of the invariant, the completed-command rule and the exit fallback all fail it.
- 2026-10-12: Review round 5 (review of `358dca6`, one major finding). A successful
  `compose down` was treated as proof of removal, but Compose returns success while a network or
  volume is still in use, so normal teardown and the reaper could release or unlink a lock while
  a resource remained. The single release point now requires a fresh listing to show none of the
  stack's containers, volumes or networks (a failed listing counts as not proven) for any stack
  whose creation was attempted. One helper, `_remove_and_prove`, runs `down` and the listing a
  bounded number of times and is used by teardown, the exit fallback and the reaper; ownership is
  cleared and the reaper reports success and unlinks its claim only after that proof. The table
  gains rows where `down` succeeds but leaves a container, network or volume (ownership and lock
  kept, the exit fallback then retries), a transient removal failure, and a repeated one; reaper
  unit tests cover the same three leftovers. Removing the proof from the release point, from the
  helper, from the teardown or from the reaper each fails the new tests.
- 2026-10-12: Independent review round 6 approved `c679a79` with no findings. The branch was
  rebased onto main `a10645a` (T130 #297) as twelve unchanged commits and force-pushed with
  lease, as the owner authorised.
- 2026-10-12: Step (e), CI adoption. The Python shard command now runs
  `uv run pytest -m "unit or light_coverage" -n auto --dist loadgroup` with the unchanged
  sharding plugin, coverage flags and per-shard coverage upload, and the shard `timeout-minutes`
  fell from 40 to 15. `scripts/ci/validate_ci.py` pins the new command, bound and workflow
  digest, and `tests/test_ci_validation.py` rejects dropping `-n auto`, using `-n 1` or
  `--dist load`. The distributed coverage job stays serial: its PostgreSQL/S3 tests share one
  `xdist_group`, and other distribution modes would share the single provisioned database and
  bucket. Parallel-run hazards settled before adoption: the argon2 medians test measures the
  CPU time of the measuring thread instead of wall time, with the same samples and threshold;
  the broker factory tests take the real authority lock under a per-test directory name; the
  interruption-table helper thread no longer reads `time.monotonic`, which a later test on the
  same worker could have patched, and is stopped and joined; and a shared fixture removes
  application log handlers a test installed, since one bound to a closed capture stream made
  `test_cli_conversion_lifecycle_idempotency_capacity_authorization_and_downloads` fail
  intermittently under xdist. Locally, `tests/unit` plus `tests/integration/auth` passed five
  times out of five with `-n auto` while the production broker lock was held, and both CI
  shards passed five times out of five with `-n auto --dist loadgroup` and CI coverage flags
  (shard 0 3,060 tests in about 44 s, shard 1 2,697 tests in about 58 s on the 4-vCPU host).
- 2026-10-12: First PR CI run with parallel shards (38280813643 on `926817a`): shard 1 passed
  in 1m56s and distributed coverage in 2m58s; shard 0 failed in 2m14s because
  `tests/unit/composer/test_egress_guards.py::test_busy_slot_and_invalid_json_result` ran with
  the fixture's 50 ms provider budget, which building a TLS context exceeded under four busy
  workers. The fixture now uses a generous 5 s budget; tests that exercise deadlines already set
  their own. `CI / light` failed in the unrelated web test
  `tests/composer-generation-workspace-coverage.test.tsx` ("a rejected template version requires
  an explicit refreshed choice before retry"), which also failed once on `e5543f7` and passed
  locally and on `358dca6`; T98 does not touch `web/`.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-630.
