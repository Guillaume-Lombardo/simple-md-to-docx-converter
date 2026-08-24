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
- 2026-08-24: Addressed independent-review findings by invoking the complete T10 activation
  boundary with caller-declared fonts and immutable validation evidence; freezing only an active,
  current, published template/version pair during job submission; verifying stored size and SHA-256
  for downloads, restore, and processing; and adding durable pending-publication and deletion
  tombstones reclaimed at startup. Added database owner/pair/current/deletion invariants, audited
  preference set/clear, required T18-owned bounds and engine configuration, async HTTP offload,
  stable 413/422 and integrity errors, binary OpenAPI responses, and the exact frozen-version
  processor adapter. Verification completed with 689 unit tests, 848 applicable default tests at
  94.24% total coverage and 92.41% changed-Python-line coverage, 13 live PostgreSQL/RustFS tests,
  and 3 T15 real Pandoc/LibreOffice tests in the rootless toolchain image. The host-only unfiltered
  command remains unable to execute engine
  tests because those binaries are intentionally supplied by the toolchain image; full legacy
  image-suite validation remains owned by its existing T10/T20 environment rather than this ticket.
- 2026-08-24: The second correction pass added configurable, expiring publication leases with
  atomic SQLite/PostgreSQL claims and token-fenced finalization, abort, and retry release; SQLite
  immediate-write serialization for template mutation versus job submission; concrete production
  worker composition that cannot bypass frozen-version integrity verification; PostgreSQL coverage
  for every migration-05 relational invariant; and submission races against replacement, archive,
  and deletion on both profiles. The exact unit gate passes 699 tests with the required 90% branch
  threshold, the applicable live PostgreSQL/RustFS host suite passes 860 tests, 20 repeated SQLite
  race runs pass, and all 3 T15 real-engine tests pass in the arbitrary-UID rootless toolchain.
  K3s remained inactive. The committed Git tree passes 90.05% application branch coverage
  (1,041/1,156) and 91.95% changed executable-line coverage (811/882).

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
