---
ticket: T15
linear_id: G1L-323
linear_url: https://linear.app/g1lom/issue/G1L-323/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T15 - Implement the versioned template API

## Objective

Implement downloads, ETag and If-Match, atomic replacement, copy-forward restoration, audit, and profile parity.

## Acceptance criteria

- The implementation satisfies the T15 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- T10
- T14

## Progress

- 2026-08-24: T10 and T14 are verified `Done` locally and in Linear. Implementation started on
  `feat/T15-versioned-template-api` from delivered main `74dcba5`. Scope is the owner/admin
  versioned template HTTP contract, safe content downloads, ETag/If-Match concurrency, atomic
  replacement, immutable history, copy-forward restoration, deletion/archive guards, audit, and
  SQLite/PostgreSQL plus filesystem/S3 parity. T16/T17 retain the browser interfaces, and T20/T21
  retain final-image runtime wiring and E2E execution.
- 2026-08-24: Implemented the versioned `/api/v1/templates` contract with safe immutable downloads,
  strong revision ETags and required If-Match compare-and-swap mutations, metadata updates, atomic
  replacement with compensation, copy-forward restoration, archive and reference-guarded deletion,
  preferred/fallback selection, content-free audit records, and frozen processor version lookup.
  Added Alembic schema support, shared SQLite/PostgreSQL plus filesystem/S3 behavior, functional
  ASGI coverage, real standalone and distributed boundary/concurrency coverage, and English API,
  template, storage, and job documentation. Final rootless-image E2E remains T20/T21 sequencing
  debt and is not claimed by T15.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
