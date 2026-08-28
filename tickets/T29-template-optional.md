---
ticket: T29
linear_id: G1L-391
linear_url: https://linear.app/g1lom/issue/G1L-391/
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T29 - Allow conversions without a template

## Objective

Allow authenticated users to create DOCX, PDF, or combined conversion jobs without selecting or
uploading a Word template. Template-free jobs use Pandoc's native default reference document and
remain reproducible and traceable.

## Acceptance criteria

- The conversion page offers an explicit `Pandoc default` option and never requires a template.
- `POST /api/v1/conversions` accepts either no template fields or a complete template/version UUID
  pair, and rejects every partial pair.
- Template-free DOCX generation omits Pandoc's `--reference-doc` argument; PDF and combined output
  continue from that generated DOCX.
- Template-bound jobs keep freezing and integrity-checking the exact active version.
- SQLite and PostgreSQL persist a nullable template pair with a database constraint that rejects
  partial pairs.
- Idempotency distinguishes template-free jobs from every template-bound job.
- Job responses and PDF traceability identify the mode without invented template identifiers or
  hashes.
- Preferred and system fallback templates remain available but never block conversion.
- Unit, functional, integration, browser, and final-rootless-image E2E tests cover both modes and
  relevant failures in both storage profiles.
- Product, user, API, template, job, and deployment documentation describe the optional contract.

## Dependencies

- T07
- T10
- T13
- T15
- T16
- T21

## Progress

- 2026-08-28: Started after confirming that the conversion page, HTTP form, durable job model,
  database schema, frozen-template worker, Pandoc adapter, and PDF traceability all required a
  template. The approved contract uses Pandoc's native default reference document when both
  template identifiers are absent; it does not create a synthetic system template.
- 2026-08-28: Implemented the optional pair through the Web UI, HTTP API, domain, idempotency,
  SQLite/PostgreSQL schema migration, repositories, worker pipeline, Pandoc invocation, and PDF
  traceability schema v2. Added unit, JavaScript, SQLite integration, and final-image E2E coverage,
  and updated product and operator documentation.
- 2026-08-28: Ruff formatting/linting, `ty check`, JavaScript tests, and the 1,562-test local suite
  excluding unavailable PostgreSQL/RustFS services and document-engine binaries pass with 94.93%
  coverage. Final-image E2E and distributed migration execution remain for CI.
- 2026-08-28: Resolved independent-review blockers before publication: workers now read legacy PDF
  traceability schema v1 and strict schema v2 while new conversions emit v2; versioned-job
  idempotency accepts pre-T29 digests; the PostgreSQL integrity trigger only revalidates a changed
  template pair so archived-template jobs can continue; and the independent T11 raster-golden
  manifest remains schema v1. Added unit compatibility coverage and a real PostgreSQL regression
  test. Ruff, `ty`, 70 targeted tests, and all 1,570 locally runnable tests pass with 95.03%
  coverage; the canonical run only fails where PostgreSQL and RustFS services are unavailable.
- 2026-08-28: Resolved the final-review findings: manifest validation now binds schema, template
  mode, and template identity to the claimed job; v1 remains strict and versioned-only; final-image
  browser coverage exercises DOCX, PDF, and combined Pandoc-default conversions and validates their
  v2 sidecars; PostgreSQL now has a real nullable-pair persistence test; and the functional and user,
  PDF, Pandoc, and deployment documentation cover the template-free path. Expanded tests exposed
  and fixed optional-template worker logging that previously serialized a missing version as an
  invalid UUID. Ruff, `ty`, Web tests, and all 1,577 locally runnable Python tests pass with 94.99%
  coverage. PostgreSQL/RustFS and final-image execution remain assigned to CI.
- 2026-08-28: Resolved the publication review findings: the container workflow smoke test now
  validates schema-v2 mode and nullable/versioned template identity; the worker binds a versioned
  manifest's version number and SHA-256 to evidence returned by the integrity-verifying frozen
  template resolver; and residual architecture and observability text now describes both template
  modes. Ruff formatting/linting, `ty`, 23 Web tests, 74 focused tests, and all 1,580 locally
  runnable Python tests pass with 95.05% coverage. PostgreSQL/RustFS and final-image execution
  remain assigned to CI.
- 2026-08-28: Resolved the first ready-PR CI findings without weakening any gate. Unit coverage now
  exercises SQLite upgrade/downgrade, downgrade refusal, and both PostgreSQL trigger variants for
  migration 13, raising changed-line coverage from 87.98% to 96.72%. Compose and container E2E
  readers accept strict schema v1 only for the published pre-T29 versioned-image contract while
  retaining schema-v2 mode validation for new images and rejecting v1 for Pandoc-default jobs.
  Both readers require an exact integer schema and reject mixed v1/v2 fields.
- 2026-08-28: Stabilized the standalone publication-heartbeat integration test after the ready PR
  exposed a scheduler-dependent lease race. The test now waits for a real persisted heartbeat under
  a controlled clock, then advances beyond the initial lease while remaining inside the renewed
  lease instead of assuming a background thread runs within a fixed 110 ms wall-clock window. The
  regression passes 50 consecutive runs, the exact 37-test `storage-standalone` CI domain, and all
  1,596 locally runnable tests; Ruff, `ty`, and the 23-test Web suite also pass.
- 2026-08-28: PR #98 was independently approved and squash-merged as `0632d150` after every required
  CI domain and the aggregate gate passed. The source branch was removed locally and remotely, and
  Linear issue G1L-391 was marked `Done` after the merge was verified on `main`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
or progress changes.
