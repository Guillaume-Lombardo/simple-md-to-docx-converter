---
ticket: T21
linear_id: G1L-331
linear_url: https://linear.app/g1lom/issue/G1L-331/
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T21 - Run rootless E2E tests for both profiles

## Objective

Run E2E for both profiles with three identities, real conversion, restart recovery, concurrency, and failure artifacts.

## Acceptance criteria

- The implementation satisfies the T21 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.
- Both final-image storage profiles are exercised; distributed-profile environments use real PostgreSQL and RustFS through the provider-neutral AWS S3-compatible contract, never MinIO.
- The suite discharges T12's deferred final-image rootless E2E debt for primary storage workflows and relevant critical failure, restart, recovery, and concurrency behavior.
- The suite discharges T11's deferred final-image E2E debt through the final asynchronous workflow:
  successful PDF conversion and download, engine/output failure, cancellation without publication,
  concurrent isolated conversions, and the relevant authorization and recovery paths.

## Dependencies

- T17
- T20

## Progress

- 2026-08-23: Scope now explicitly includes T12's deferred final-image rootless E2E debt. Both profiles must run against the hardened image, with real PostgreSQL/RustFS for the distributed profile and relevant success, failure, restart, recovery, and concurrency paths.
- 2026-08-24: Scope now explicitly includes T11's approved sequencing debt. Exercise the PDF path
  through the durable T13 job workflow against the T20 image, including success, safe failure,
  cancellation, concurrency, authorization, publication, and recovery behavior.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
