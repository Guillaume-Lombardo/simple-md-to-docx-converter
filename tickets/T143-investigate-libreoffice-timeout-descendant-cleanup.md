---
ticket: T143
linear_id: G1L-696
linear_url: https://linear.app/g1lom/issue/G1L-696/t143-investigate-the-intermittent-libreoffice-timeout-descendant
status: Todo
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

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-696.
