---
ticket: T138
linear_id: G1L-691
linear_url: https://linear.app/g1lom/issue/G1L-691/t138-stabilize-the-local-test-service-interruption-tests-under-full
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T138 - Stabilize the local test-service interruption tests under full-suite load

## Objective

Make `tests/integration/test_local_test_services_interruption.py` pass inside the default
canonical pytest command. On `main` at `9306cab` and later, eight of its scenarios failed in
every full local run while passing alone and in hosted CI:

- `normal-with-detached-descendant`;
- `nonzero-with-detached-descendant`;
- `interrupt-during-up`;
- `second-interrupt`;
- `timeout`;
- `interrupt-during-timeout-stop`;
- `interrupt-before-teardown`;
- `surviving-group-before-startup`.

Each took about 55 seconds and failed with "removal of the stack could not be proven" or
`releases == []`.

## Acceptance criteria

- Identify the root cause with evidence and fix it at its source, in product, test-harness or
  fixture code. A retry or a longer timeout is not a fix.
- Keep the T98 release-point invariant in `scripts/testing/services.py` unchanged: an owner lock
  is released only when no command group is alive and removal of the stack's resources is proven.
- Add a regression test that reproduces the ordering dependency and fails without the fix.
- The module passes in several consecutive default canonical runs, serial and with `-n auto`, and
  the results are recorded.
- Every timing bound that changes stays bounded and fails on a persistent failure, and a test
  proves it.
- Apply the same diagnosis to the `test_unix_broker_transport` case seen once under load.
- The canonical checks pass.

## Dependencies

- T98 (Done).

## Blocking points

- None known.

## Progress

- 2026-10-13: Mirrored from Linear and started on branch `fix/T138-interruption-test-isolation`
  from `origin/main` `0e0f7f3`.
- 2026-10-13: Root cause. `tests/integration/test_benchmark_timeout.py::
  test_proc_root_inspection_failure_fails_closed` runs the benchmark supervisor's `main()` inside
  the pytest process. `main()` called `prctl(PR_SET_CHILD_SUBREAPER, 1)` and never restored it,
  so the pytest process stayed a child subreaper for the rest of the session. That attribute is
  process-wide. Every later orphaned descendant was then reparented to pytest instead of to init,
  and nothing in pytest reaps it. In each failing scenario, the fake `compose up` (or `ls`) starts
  a child that outlives its command leader. Once killed, that child stays a zombie in the
  command's process group. `os.killpg(group, 0)` still succeeds on a group that holds only a
  zombie, so `_group_alive` reports the group alive forever. `stop_process_group` then runs out
  its full SIGTERM and SIGKILL bounds on every attempt, `release_if_settled` correctly refuses to
  release, and the scenario fails after about 55 seconds. The invariant behaved as designed. The
  scenarios without a surviving descendant passed, and `settle-failure` expects no release anyway.
  Serially, `tests/integration/test_benchmark_timeout.py` runs just before this module, which
  explains why the module passed alone, with the unit tests, and with the PostgreSQL, S3 or
  lifecycle suites, but failed in every full run. Hosted CI shards run the two files in different
  processes.
- 2026-10-13: Minimal reproduction on `origin/main`: running
  `test_proc_root_inspection_failure_fails_closed` followed by
  `test_every_exit_path_keeps_the_ownership_invariant[normal-with-detached-descendant]` fails the
  second test in 55 seconds with "removal of the stack could not be proven".
- 2026-10-13: Second isolation defect found in the same fixture and in the lifecycle unit
  fixture of `tests/unit/test_local_test_services.py`. Under the default command, the PostgreSQL
  and S3 suites run earlier and the session-scoped local service stack stays owned in the same
  process. Both fixtures' teardowns closed and cleared every held lock, including that live
  stack's. This released its `flock` mid-session and recorded fake command groups in its lock
  file. The file then stayed in the lock directory after the session removed the stack.
  Reproduced on `origin/main` with one PostgreSQL test followed by the `normal` scenario: the
  session's lock file remained after the run. After fixing only the scenario fixture, a full
  serial run with a private `XDG_RUNTIME_DIR` still left one lock file, which led to the unit
  fixture.
- 2026-10-13: Fixes.
  - `scripts/javascript/run_bounded_benchmark_command.py` scopes the subreaper attribute to the
    supervision: it reads the previous value with `PR_GET_CHILD_SUBREAPER` and restores it when
    `main()` returns.
  - `tests/conftest.py` adds the autouse guard `keep_child_subreaper_state`. A test that leaves
    the process a subreaper fails at its own teardown, and the previous value is restored, so a
    later leak cannot move to an unrelated test again.
  - The new helper `tests/local_service_bookkeeping.py` sets aside the ownership tables of an
    already owned session stack, closes only the test's own locks, and restores the outer entries
    untouched. Both service test fixtures use it.
  - No timing bound and no release condition changed.
- 2026-10-13: Regression tests.
  - `tests/test_test_harness.py::test_child_subreaper_leak_fails_its_own_test_and_is_undone`
    runs a test that leaks the subreaper attribute, followed by one that stops a command group
    whose descendant outlived its leader. It runs serially and under xdist. Without the guard, the
    second test fails as the interruption scenarios did. With the guard, only the leaking test
    fails, at its teardown. The existing
    `test_benchmark_timeout.py::test_proc_root_inspection_failure_fails_closed` now proves the
    supervisor fix, because the guard fails it without that fix.
  - `test_a_session_stack_keeps_its_ownership_across_a_scenario` and
    `test_isolated_bookkeeping_closes_only_its_own_locks` fail when the outer bookkeeping is not
    restored.
- 2026-10-13: Two unrelated `/proc` races surfaced in the repeated runs. Reading
  `/proc/<pid>/stat` of a process that is reaped during the read raises `ProcessLookupError`
  (ESRCH), not `FileNotFoundError`. This failed
  `test_engine_version_probe.py::test_cancellation_kills_a_sigterm_resistant_descendant` in 2 of
  5 serial runs. Its liveness helper and the interruption module's now treat that error as
  "gone".
- 2026-10-13: `test_unix_broker_transport` does not share the subreaper cause. It starts no
  subprocess and checks no process group; its tests use threads and Unix sockets with deadlines,
  and in serial order it runs before `test_benchmark_timeout.py`.
  `test_real_workspace_stage_collect_pending_and_success_exchange` (1-second operation timeout)
  failed once in an `-n auto` run with a connection reset. That is a load-timing question for the
  broker area, which T137 owns.
- 2026-10-13: Coordination. T139 removes `scripts/javascript/run_bounded_benchmark_command.py` and
  `tests/integration/test_benchmark_timeout.py`. If T139 merges first, this branch resolves the
  conflict by accepting the deletion. The guard and its harness test do not depend on the
  supervisor.
- 2026-10-13: Verification at `cecf2a7` on a 4-CPU, 13 GB machine, with other worktrees running
  their own suites in parallel during the earlier rounds.
  - Default canonical command, serial: 3 consecutive runs, each with 6,122 passed and 0 failed,
    in about 470 to 490 seconds. Coverage was 94.3%.
  - With `-n auto`: 1 run with 6,122 passed and 0 failed, in 191 seconds.
  - One more serial run with a private `XDG_RUNTIME_DIR` passed (6,122) and left no lock file.
  - Before that head, at `e4be940`/`e462a9a`, the interruption module passed in all six full runs.
    The other failures in those runs were unrelated: two at a system load average near 97
    (a recovery timeout and an SQLite migration lock), one broker transport reset, one fake Pandoc
    timeout, and the ESRCH race above.
  - `ruff format --check`, `ruff check` and `ty check` pass.
