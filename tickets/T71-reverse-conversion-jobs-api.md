---
ticket: T71
linear_id: G1L-539
linear_url: https://linear.app/g1lom/issue/G1L-539/t71-add-persistent-reverse-conversion-jobs-and-api
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T71 - Add persistent reverse-conversion jobs and API

## Objective

Expose reverse conversion as an authenticated asynchronous FastAPI workflow with persistent jobs,
owner isolation, both storage profiles, and deterministic Markdown-package downloads.

## Acceptance criteria

* Add a versioned `/api/v1/reversions` contract for multipart submission, owner-scoped
  listing/status, cancellation, and result download; submission returns `202 Accepted`, `Location`,
  and `Retry-After`, and supports `Idempotency-Key`.
* Extend the installed HTTP-only conversion/job CLI family with reverse submission, listing,
  status, cancellation, and result download while preserving its authentication, CSRF,
  idempotency, retry, output, and owner-only local-profile contracts.
* Reuse the persistent queue, lease, heartbeat, attempt, recovery, expiration, capacity, and cleanup
  invariants without weakening or ambiguously overloading forward-conversion behavior.
* Persist the source format, original safe filename stem, anydoc/component version, output digest
  and size, traceability metadata, states, steps, and stable failures in SQLite/filesystem and
  PostgreSQL/S3 with shared contract tests.
* Route uploads through bounded request reading and the configured ClamAV or explicitly approved
  trusted-upstream boundary before durable reservation or object persistence.
* Enforce source/status/cancellation/result ownership on every lookup; administrators receive no
  implicit document-content access unless the existing normative policy explicitly grants it.
* Always return a ZIP package when assets are present and define the approved plain-Markdown versus
  ZIP behavior for asset-free results; preserve safe filename and private no-store/nosniff download
  headers.
* Add configuration for approved source/result size, asset count/bytes, active jobs, queue capacity,
  duration, retention, and cleanup values without silently changing existing forward-conversion
  limits.
* Extend deterministic OpenAPI compatibility validation, observability, metrics, and safe
  structured logs for the new job family.
* Add unit, functional, real SQLite/filesystem, real PostgreSQL/S3, restart, recovery, concurrency,
  cancellation, expiration, authorization, idempotency, scanner, and failure coverage.

## Dependencies

* T13
* T19
* T45
* T70

## Implementation boundary

* Own reverse-job domain/application services, API schemas/routes, HTTP-only CLI parity,
  persistence migrations and adapters, storage keys, worker orchestration, policy/configuration,
  OpenAPI, and backend tests.
* Do not implement the browser workspace or OCR.
* Preserve the existing forward-conversion API and data contract.

## Quality requirements

* Maintain both storage profiles, queue safety, scanner ordering, content-free logs, and existing
  resource budgets.
* Cover every new real boundary with integration tests and retain the repository coverage
  thresholds.
* Keep repository artifacts and API text in English.

## Progress

* 2026-09-03: Created from the approved feasibility decomposition; blocked by T70.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
