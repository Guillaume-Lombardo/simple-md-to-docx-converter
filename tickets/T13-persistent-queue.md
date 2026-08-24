---
ticket: T13
linear_id: G1L-322
linear_url: https://linear.app/g1lom/issue/G1L-322/
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T13 - Implement the persistent queue and conversion workers

## Objective

Implement the persistent queue, job API, state machine, idempotency, workers, leases, heartbeat, recovery, and cleanup.

## Acceptance criteria

- The implementation satisfies the T13 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- T11
- T12

## Progress

- 2026-08-24: T11 and T12 are verified `Done` in both the repository and Linear. Implementation
  started on `feat/T13-persistent-queue-workers` from delivered main `80974b3`. Scope is the shared
  durable job state machine and repository contract, SQLite/PostgreSQL claim and lease semantics,
  idempotent owner-scoped submission, embedded/external worker coordination, heartbeat, restart
  recovery, cancellation, atomic result publication, and safe job HTTP endpoints. T18 retains all
  unresolved production limits, quotas, retention, and cleanup schedules; T20/T21 retain final-image
  E2E and T15 retains versioned template mutation APIs.
- 2026-08-24: Implemented the storage-neutral job model, owner-scoped idempotent submission,
  authenticated conversion API, SQLite/PostgreSQL queue repository and Alembic schema, transactional
  claims, leases, heartbeats, deterministic cancellation/recovery/expiration, atomic result
  publication, and bounded embedded/external worker loops. The real SQLite restart contract,
  PostgreSQL `SKIP LOCKED` concurrency, and filesystem-backed ASGI workflow passed initial
  validation before independent review. Final-image E2E remains sequenced to T20/T21; T15 still
  owns the real template-version processor connection and T18 owns approved production timing,
  quota, retention, and cleanup values.
- 2026-08-24: Resolved the first independent review. Every claim and cleanup batch now uses a
  unique fencing token; result identifiers are unique per attempt; heartbeat and recovery are
  periodic; cancellation wins atomically; source reservations precede object writes; oversized
  request bodies are rejected before multipart parsing; component and expiration traceability is
  exposed through the API; and transient worker failures are retried or surfaced to supervision.
  Added real worker integration through SQLite/filesystem and PostgreSQL/S3, concurrent PostgreSQL
  idempotency, stale-lease, cancellation-race, long-stage heartbeat, cleanup-claim, source-recovery,
  OpenAPI binary-response, and pre-parser request-bound tests. Formatting, Ruff, and `ty` pass. The
  unit suite passes 655 tests at 93.55% coverage, and the applicable canonical suite passes 805
  tests at 94.73% coverage with real PostgreSQL and RustFS. The previous unfiltered host run passed 809
  tests; its 34 failures are exclusively marked Pandoc, Mermaid/Chromium, LibreOffice, and locked
  font tests because those engines are not installed on the host. Final independent specification
  and security reviews approve the implementation; the test review approves it after this mechanical
  validation-count correction. All three explicitly approve the T20/T21 E2E sequencing exception.
- 2026-08-24: Published ready pull request
  [#44](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/44) after the branch
  was rebased on `origin/main`. Awaiting required GitHub checks before authorized squash merge.
- 2026-08-24: Pull request #44 passed every required GitHub check, including the document-engine,
  functional, standalone-storage, distributed-storage, and protected gate jobs. It was
  squash-merged as `bb5c1d0` and verified on `main`; the exact source branch was removed locally
  and remotely. The approved final-image E2E sequencing remains tracked by T20/T21 and is not a
  waiver of that validation.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
