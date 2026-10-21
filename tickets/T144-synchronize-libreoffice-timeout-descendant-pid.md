---
ticket: T144
linear_id: G1L-697
linear_url: https://linear.app/g1lom/issue/G1L-697/t144-synchronize-descendant-pid-publication-in-the-libreoffice-timeout
status: Done
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
- 2026-10-21: Started on branch `fix/T144-libreoffice-timeout-pid` from `origin/main` `95bc188`.
- 2026-10-21: Fix (test-only; no product code changed).
  - **Readiness gate in the timeout case.** The `timeout` parametrization now passes a probe that
    holds the converter's first poll after the engine starts until the descendant pid is published
    (bounded at 10 s), then returns `False`. `LibreOfficePdfConverter._wait` calls the probe before
    every deadline check, so the 0.3 s timeout can no longer fire before publication, and the run
    still ends through the real deadline with `PDF_TIMEOUT`. The probe call that precedes the engine
    start (before the converter workspace holds `source.docx`) returns at once.
  - **Shared exit helper in `test_template_activation.py`.** Its local Z-only predicate treated a
    task being reaped (`X`) as running, did not handle an ESRCH read, and re-read `/proc` after the
    wait. It now uses `tests.process_exit.wait_for_process_exit` with the same 2 s bound.
- 2026-10-21: Evidence on a 4-vCPU container, fake engine, with the T143-style harness
  (16 concurrent pytest instances of the `timeout` case, 8 CPU hogs, 15 rounds):
  - `main` `95bc188`: 2 of 240 iterations failed, both on the missing pid file, none on a survivor.
  - this branch: 0 of 240 failed.
  - Deterministic check: delaying the pid publication by 0.6 s, beyond the 0.3 s timeout, passes
    with the gate and fails with `FileNotFoundError` when the gate is replaced by a plain probe.
  - A descendant that calls `os.setsid()` to leave the process group fails all three LibreOffice
    parametrizations ("A descendant survived process-group cleanup.") and the template activation
    test ("Timed-out engine descendant survived process-group cleanup").
- 2026-10-21: Canonical checks. `ruff format --check`, `ruff check` and `ty check` pass. The default
  local command ran 6,222 passed, 9 failed and 78 errors in 11 min 17 s. None involve the changed
  files: the 78 errors are PostgreSQL, S3 and local-service tests that need a Docker daemon, which
  this container lacks; 8 of the failures reproduce identically on unmodified `main` (subreaper,
  `/proc` memory, doctor and broker factory tests restricted by the container sandbox); the ninth,
  `test_pandoc_docx.py::test_real_process_boundary_normalizes_failures[...invalid_docx]`, is an
  intermittent `PANDOC_TIMEOUT` under load (1 of 5 reruns) that this change does not touch.
  Hosted `document-engines` evidence (three consecutive passes) remains to be collected on the
  pull request.

- 2026-10-21: Verified on `main` and synchronized with G1L-697 (`Done`).
  [PR #321](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/321) was squash-merged as
  [`09b37c29068280936b436afd8a477e8c3203fb38`](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/commit/09b37c29068280936b436afd8a477e8c3203fb38); its tree matches
  reviewed head [`7111baf27aaefffea1c38cebecf029a5393ea349`](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/commit/7111baf27aaefffea1c38cebecf029a5393ea349).
  [Final reviewed-head CI run 39601956015](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/39601956015)
  passed all required checks. The successful hosted checks complete the external-engine
  qualification that was unavailable in the earlier local environment; the recorded stress and
  surviving-descendant evidence remains unchanged.
  Three consecutive completed hosted `document-engines` executions passed with identical
  changed test code: [first](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/39599605984/job/119220863654),
  [second](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/39599605984/job/119223480212)
  and [third, on the final head](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/39601956015/job/119229179047).
  A superseded third attempt cancelled by the rebase is not counted as a pass.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-697.
