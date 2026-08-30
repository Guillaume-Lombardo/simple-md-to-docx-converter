---
ticket: T33
linear_id: G1L-408
linear_url: https://linear.app/g1lom/issue/G1L-408/t33-add-conversion-and-job-cli-commands
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T33 - Add conversion and job CLI commands

## Objective

Expose conversion submission and the complete user-owned job lifecycle through HTTP-only `markweave` commands.

## Acceptance criteria

* Implement `markweave convert` plus job list, show, wait, cancel, result download, and manifest download commands.
* Use only `/api/v1` HTTP endpoints and the authenticated CLI profile; do not access repositories, object stores, or workers directly.
* Support Markdown and ZIP input, DOCX/PDF/both output, optional or exact versioned templates, idempotency keys, polling guidance, and bounded waits.
* Write downloads atomically, refuse unsafe overwrite by default, preserve safe server errors and correlation IDs, and never log document content or source names.
* Cover success, quota/capacity, authorization, cancellation, expiration, retry, interrupted download, and both storage profiles through unit, integration, and final-image E2E tests.

## Dependencies

* T32
* T13
* T29

## Implementation boundary

* Own only T31's pre-registered conversion/job family modules and domain tests/documentation; do not edit the root registry, shared help snapshots, documentation index, or other command families.
* Do not change server business behavior except for a separately documented contract defect that blocks the HTTP client.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up restricted this worker to T31's conversion/job family and excluded shared registry, help, and documentation-index files.
* 2026-08-30: Implementation started on `feat/T33-conversion-job-cli` from verified `main` at `c1cae3b`. Scope remains limited to the pre-registered conversion/job command family, its domain-specific tests, and dedicated CLI documentation.
* 2026-08-30: Implemented HTTP-only conversion submission, owner-scoped job inspection/control, bounded polling, safe idempotent retry, and atomic result/manifest downloads with dedicated unit, real-HTTP integration, and final-image drivers.
* 2026-08-30: Verified 1,216 unit tests and 1,971 locally runnable tests at 95.19% branch coverage; the focused T33 modules reach 91%. The exact final image passed the standalone and distributed T33 drivers. The canonical suite remains externally blocked by absent PostgreSQL/S3 test settings, and the existing distributed API smoke fails after the T33 driver on `mermaid_unavailable`.
* 2026-08-30: Kept `tests/unit/test_cli.py` and shared container smoke scripts identical to the branch base pending the approved T34 -> T33 -> T35 integration order; final shared-runner wiring is deferred until T34 is merged into `main`.
* 2026-08-30: Integrated current `main` through T34 and the T42 worker decomposition without conflicts, removed only T33's obsolete shared placeholder cases, and added exactly one conversion-driver invocation to the final-image runner.
* 2026-08-30: Verified the complete standalone and distributed final-image runners on post-T42 image `a520ed460bf749e5acb26e629de5eb1add2b81290e2b7517e4295698715800e1`; both T33 drivers and all surrounding security, browser, template, restart, recovery, and checkpoint workflows passed. Current `main` at `8f3b792` is an ancestor of the implementation branch.
* 2026-08-30: Addressed the independent CodeRabbit request changes by opening conversion sources once with no-follow descriptor semantics and validating and reading that same descriptor, and by anchoring download validation, temporary-file creation, publication, cleanup, and directory synchronization to one no-follow parent-directory descriptor. Added deterministic source-swap and parent-replacement regression tests.
* 2026-08-30: Reverified 34 focused tests at 91% branch coverage, 126 T33/shared workflow tests, Ruff formatting and linting, `ty`, and shell syntax. The rebuilt final image `fbca168bce652aa3b5f511000238f83a0a5b71c10bab6b1d73003df6dfa2665c` passed the complete standalone and distributed runners, including security, API, conversion CLI, browser, restart, recovery, and checkpoint workflows; exact generated artifacts and image tags were removed afterward.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
