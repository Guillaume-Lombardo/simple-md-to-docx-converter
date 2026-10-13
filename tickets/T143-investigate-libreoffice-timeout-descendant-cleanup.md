---
ticket: T143
linear_id: G1L-696
linear_url: https://linear.app/g1lom/issue/G1L-696/t143-investigate-the-intermittent-libreoffice-timeout-descendant
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T143 - Investigate the intermittent LibreOffice timeout descendant cleanup failure

## Problem

Hosted run 38306066586 (T135 head 62d72fc), job `CI / document-engines`, failed on `tests/integration/document_engines/test_libreoffice_pdf.py::test_timeout_and_cancellation_kill_process_group_descendants[timeout]`.

Two seconds after the timeout path, `_wait_for_descendant_exit` (line 386) still found the descendant process running: its state was not `Z`, and `/proc/<pid>` existed. The other 294 tests in the job passed, and the job passed in every other recorded run. T135 does not touch document engines or this test.

This is a security-relevant subprocess area. If the LibreOffice timeout path can leave a live descendant, an engine process can outlive its job.

## Scope

- **Decide between a product leak and a measurement race**, using the job log and the code of the LibreOffice runner's timeout and process-group kill.
  - Can the descendant escape the process group, for example through a new session, a double fork or re-parenting?
  - Can the kill be skipped or race the spawn?
  - Is the test's 2-second window too short for a SIGKILL under CI load? This is unlikely for SIGKILL, but check it.
- **Compare with related fixes:**
  - T138 made `/proc` liveness helpers treat ESRCH as gone (`test_engine_version_probe`).
  - T137 bounded process reaping.
- **Fix the root cause.** If the product leaks a descendant, fix the kill path and add a regression test that fails without the fix. Do not lengthen the wait without evidence.

## Acceptance criteria

- The root cause is documented with evidence.
- The test and the product guarantee — no descendant survives a timeout or cancellation — hold in repeated runs:
  - run the test in a loop locally, with real LibreOffice or the fake engine;
  - get three consecutive hosted `document-engines` passes.
- The canonical checks pass.

## Dependencies

- None.

## Progress

- 2026-10-13: Created from the closure review of the T135 evidence runs.
- 2026-10-13: Root cause: a measurement race in the test helper, not a product leak.
  - **The kill path is sound.**
    - The fake engine runs in its own session (`start_new_session`). Its descendant never calls `setsid` or `setpgid`, so it cannot leave the process group.
    - `_terminate_group` sends SIGTERM to the group, waits out the grace period with `killpg(pgid, 0)`, then sends SIGKILL to the group.
    - In a stress harness (16 parallel instances against 8 CPU hogs, 240 iterations), every SIGKILLed descendant was gone within 14 ms.
  - **The helper misreads a process being reaped.**
    - `/proc/<pid>/stat` shows a killed process as `Z` (zombie), then briefly as `X` (EXIT_DEAD) while its reaper runs, and then the entry disappears.
    - The old `_wait_for_descendant_exit` treated `X` as running. It also read `/proc` twice: once in the loop condition and again in the final assertion.
    - When the loop saw `Z` and the assertion read caught `X`, the helper reported a dead descendant as alive without waiting at all.
    - The ticket's description of the failure fits this: the state was not `Z` and `/proc/<pid>` existed.
  - **The hosted log supports this.**
    - In run 38306066586, the progress line before the timeout, cancel and probe-failure parametrizations is stamped 20:45:53.2251. The line that reports them (`F..`) is stamped 20:45:54.7304.
    - All three finished within 1.505 s, against 1.37 s locally, so the 2-second wait loop never ran.
  - **The race reproduces locally.**
    - A C probe that SIGKILLs a child and polls its `/proc` state saw the sequence `Z` then `X` in 4187 of 20000 iterations.
    - A Python reproduction of the old predicate and double read, with a separate reaper process, produced 71, then 98, false "still running" failures in 20000 iterations each.
    - The new helper produced 0 failures in 20000 iterations.
- 2026-10-13: Fix in `fix/T143-libreoffice-descendant-cleanup`.
  - **New helper `tests/process_exit.py`.**
    - It treats `Z`, `X`, a missing entry and ESRCH as exited, consistent with T138.
    - It decides from the first exited observation and never re-reads, so a PID reused later cannot turn the result back to running.
  - **Unit tests in `tests/test_process_exit.py`.** With the old semantics restored, 5 of the 13 tests fail: the latched sequence, the `Z`, `X` and missing cases, and waiting through `R`, `D`, `Z`.
  - **Wait unchanged.** The test keeps its 2-second wait, now through the helper.
  - **No product code changed.**
- 2026-10-13: Local evidence. LibreOffice, Pandoc and Mermaid are not installed, so these runs use the fake engine.
  - The three descendant parametrizations passed in 50 of 50 serial runs.
  - The document-engine integration suite and the helper tests passed in 50 of 50 runs under `-n auto` (4 workers) with 4 CPU hogs: 131 passed each time.
- 2026-10-13: Follow-up candidates, out of scope here.
  - `test_template_activation.py` has a similar Z-only liveness predicate. It stops at the first exited observation, so it cannot produce this false failure.
  - In this test's `timeout` mode, the 0.3 s timeout can fire before the fake engine publishes the descendant pid on a heavily starved host. The stress harness hit this in 7 of 240 iterations. It would fail as a missing pid file, not as a surviving descendant.
- 2026-10-13: Hosted evidence on PR #308, head 2b0b603. The `CI / document-engines` job passed three consecutive times in run 38439655886, with 295 passed each time:
  - attempt 1: job 115442704111;
  - attempt 2: job 115444345531;
  - attempt 3: job 115445679165.
- 2026-10-13: Done. Merged as `2df5640` (PR #308) after independent review; main CI run 38443008178 passed. Follow-up: T144 synchronizes the descendant pid publication in the timeout case and reviews the Z-only predicate in `test_template_activation.py`.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-696.
