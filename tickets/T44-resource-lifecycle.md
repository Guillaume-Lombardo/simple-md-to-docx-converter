---
ticket: T44
linear_id: G1L-420
linear_url: https://linear.app/g1lom/issue/G1L-420/t44-eliminate-database-resource-lifecycle-warnings
status: In Progress
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

* 2026-08-30: A final three-pass lifecycle stress selection passed 22 tests per run; each run included the four-thread/12-engine SQLite regression, application and embedded-worker failure teardown, distributed fixture cleanup, functional conversion API lifecycle, and concurrent metrics-server shutdown.
* 2026-08-30: Implemented deterministic application-owned SQL/S3 cleanup, managed test-engine lifecycles, streaming-body and HTTP-error closure, partial-startup cleanup, and targeted `ResourceWarning`/unraisable-finalizer enforcement. Reconciled the work with verified `main` at `66a0cff` while preserving canonical `MARKWEAVE_*` test configuration. Focused lifecycle coverage passes 272 tests; the locally runnable canonical matrix passes 1,666 tests with 95.16% application coverage, Ruff and `ty` pass, and all 23 Web tests pass. PostgreSQL/RustFS and Pandoc/Mermaid/Chromium/LibreOffice validation remains unavailable locally because their required services and executables are absent; exact-main CI `33302570018` passed before this branch was reconciled.
* 2026-08-30: Started implementation on `fix/T44-resource-lifecycle` from verified `main` at `381e74e9`; this workstream exclusively owns deterministic component/database lifecycle fixes, targeted warning enforcement, and leak regressions before T41/T43.
* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
