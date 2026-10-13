---
ticket: T144
linear_id: G1L-697
linear_url: https://linear.app/g1lom/issue/G1L-697/t144-synchronize-descendant-pid-publication-in-the-libreoffice-timeout
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T144 - Synchronize descendant pid publication in the LibreOffice timeout test

## Problem

In `tests/integration/document_engines/test_libreoffice_pdf.py::test_timeout_and_cancellation_kill_process_group_descendants[timeout]`, the 0.3 s engine timeout can fire before the fake engine publishes its descendant's pid file. On a heavily starved host the test then fails because the pid file is missing, not because a descendant survived. The T143 stress harness (16 parallel instances, 8 CPU hogs) hit this in 7 of 240 iterations. Hosted CI has not reproduced it yet.

The independent review of T143 (PR #308) raised this as a minor follow-up.

## Scope

- Make the timeout case deterministic. The fake engine must publish the descendant pid before the timeout can fire, for example through a readiness handshake the test waits on, or a timeout that starts only after readiness. The timeout path must still be exercised for real.
- Review the similar Z-only liveness predicate in `test_template_activation.py`, and move it to the shared `tests/process_exit.py` helper if that is appropriate.
- Keep the guarantee that no descendant survives a timeout or cancellation, and keep the test failing when one does.

## Acceptance criteria

- The stress harness from T143 shows no missing-pid failures across at least 240 iterations.
- A deliberately surviving descendant still fails the test.
- The canonical checks pass, and three consecutive hosted `document-engines` runs pass.

## Dependencies

- T143 (G1L-696), which introduces `tests/process_exit.py`.

## Progress

- 2026-10-13: Created from the minor finding of the T143 independent review.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-697.
