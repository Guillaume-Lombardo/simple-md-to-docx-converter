---
ticket: T33
linear_id: G1L-408
linear_url: https://linear.app/g1lom/issue/G1L-408/t33-add-conversion-and-job-cli-commands
status: Backlog
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

* Own only conversion/job CLI modules and their tests/documentation.
* Do not change server business behavior except for a separately documented contract defect that blocks the HTTP client.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.

