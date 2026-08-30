---
ticket: T44
linear_id: G1L-420
linear_url: https://linear.app/g1lom/issue/G1L-420/t44-eliminate-database-resource-lifecycle-warnings
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T44 - Eliminate database resource lifecycle warnings

## Objective

Close every database and component resource deterministically and turn current unclosed-resource warnings into enforced regressions.

## Acceptance criteria

* Reproduce and eliminate the SQLite `ResourceWarning` cases observed in functional, integration, and release tests.
* Make application, repositories, engines, fixtures, threads, cursors, temporary files, and failure paths expose and use deterministic close/dispose lifecycles.
* Ensure cleanup remains bounded and safe during partial startup, cancellation, exceptions, and test teardown.
* Enable targeted warning-as-error enforcement for `ResourceWarning` without hiding legitimate third-party warnings globally.
* Add regression tests and run repeated/parallel test selections to detect lifecycle leaks.

## Dependencies

* T06
* T12

## Implementation boundary

* Own deterministic component/database lifecycle fixes and warning enforcement.
* Complete before T41 and T43 touch the same lifecycle and persistence components.

## Progress

* 2026-08-30: Completed through pull request #112, squash-merged on `main` as `059ee925f9623e383057ca4ac5161a28655864da`; full CI run `33306831377` passed on the exact merged revision.
* 2026-08-30: Independent review approved T44 at `1e7e2f6`. Reconciled the T32 closure on current `main` at `2c23829` through normal merge commit `24ab0c6`; the merge adds only `tickets/T32-secure-cli-http-profiles.md`, and all seven T44 commits remain identical in `git range-diff`, with no conflicts or semantic delta. Post-merge validation passes 150 targeted T44 tests, 27 documentation/CLI tests, Ruff, `ty`, and diff validation.
* 2026-08-30: Corrected the previous review note, which verified normal-path closure but overstated repository-wide failure safety. Audited all 97 test constructor sites (76 database-engine calls, nine component builds, and 12 S3 stores). Every real SQLAlchemy engine is now registered with pytest teardown at the underlying creation boundary before a test or helper regains control; successful component builds used by assertion-heavy or multi-build tests register immediate teardown; the remaining direct S3 acquisition wraps all operations in `try/finally`; and distributed administration assembly closes its built component graph if template or application construction fails. The focused constructor/lifecycle matrix passes 123 tests, all 52 externally marked affected tests collect, and a three-pass stress selection passes 25 tests per run. The complete locally runnable matrix passes 1,666 tests with 95.21% application coverage; Ruff, `ty`, and diff validation pass. PostgreSQL/RustFS and real document-engine execution remain unavailable locally.
* 2026-08-30: Addressed independent review findings with a repository-wide audit of test-created components, database engines, and S3 stores. Reconciled the revision with current `main` at `d3c7a2f` without lifecycle conflicts. Injected application components now close explicitly; all seven real RustFS/S3 test stores close through `finally` blocks or pytest-managed finalizers; distributed engines are registered for teardown before setup can fail; real document-engine SQLite services dispose on setup, assertion, and engine failures; and mock-backed lifecycle tests now express the same ownership contract. The affected local selection passes 63 tests, and a three-pass stress selection passes 24 tests per run. Ruff, `ty`, collection of all 52 externally marked affected tests, and diff validation pass. The locally runnable matrix produced 1,664 passes with 95.16% coverage; two unrelated release-process deadline tests failed only during the loaded run and both passed together in isolation. PostgreSQL/RustFS and real document-engine execution remains unavailable locally.
* 2026-08-30: A final three-pass lifecycle stress selection passed 22 tests per run; each run included the four-thread/12-engine SQLite regression, application and embedded-worker failure teardown, distributed fixture cleanup, functional conversion API lifecycle, and concurrent metrics-server shutdown.
* 2026-08-30: Implemented deterministic application-owned SQL/S3 cleanup, managed test-engine lifecycles, streaming-body and HTTP-error closure, partial-startup cleanup, and targeted `ResourceWarning`/unraisable-finalizer enforcement. Reconciled the work with verified `main` at `66a0cff` while preserving canonical `MARKWEAVE_*` test configuration. Focused lifecycle coverage passes 272 tests; the locally runnable canonical matrix passes 1,666 tests with 95.16% application coverage, Ruff and `ty` pass, and all 23 Web tests pass. PostgreSQL/RustFS and Pandoc/Mermaid/Chromium/LibreOffice validation remains unavailable locally because their required services and executables are absent; exact-main CI `33302570018` passed before this branch was reconciled.
* 2026-08-30: Started implementation on `fix/T44-resource-lifecycle` from verified `main` at `381e74e9`; this workstream exclusively owns deterministic component/database lifecycle fixes, targeted warning enforcement, and leak regressions before T41/T43.
* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Done.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
