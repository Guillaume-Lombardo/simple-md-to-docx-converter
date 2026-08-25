---
ticket: T21
linear_id: G1L-331
linear_url: https://linear.app/g1lom/issue/G1L-331/
status: Done
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
- The suite discharges T06's deferred final-image authentication debt for login, session,
  authorization, revocation, expiration, cookie, administrator account management, and recovery
  behavior with two regular users and one administrator through Playwright.
- The suite discharges T11's deferred final-image E2E debt through the final asynchronous workflow:
  successful PDF conversion and download, engine/output failure, cancellation without publication,
  concurrent isolated conversions, and the relevant authorization and recovery paths.
- The suite discharges T19's deferred final-image evidence by validating isolated readiness,
  scrapeable API and worker metrics for every process, account/template audit authorization and
  deterministic ordering, and audit retention across backup and restore in both profiles.
- The suite preserves browser, service, container, and conversion artifacts only when a scenario
  fails.

## Dependencies

- T17
- T20

## Progress

- 2026-08-23: Scope now explicitly includes T12's deferred final-image rootless E2E debt. Both profiles must run against the hardened image, with real PostgreSQL/RustFS for the distributed profile and relevant success, failure, restart, recovery, and concurrency paths.
- 2026-08-24: Scope now explicitly includes T11's approved sequencing debt. Exercise the PDF path
  through the durable T13 job workflow against the T20 image, including success, safe failure,
  cancellation, concurrency, authorization, publication, and recovery behavior.
- 2026-08-25: Started implementation on `feat/T21-rootless-e2e` from verified `main` at `2bb098d`
  after T17 and T20 were confirmed `Done`. T20's exact implementation and completion-record main
  runs (`32794683761` and `32795330223`) passed. This workstream owns the final-rootless-image
  Playwright and service E2E matrices for standalone SQLite/filesystem and distributed
  PostgreSQL/RustFS, including the explicitly sequenced T06, T11, T12, and T19 evidence debt.
- 2026-08-25: The standalone final-image workflow passes its service, Playwright, crash recovery,
  readiness failure, restart, checkpoint, backup, and restore scenarios. Concurrent final-image
  submissions exposed and now regress a response/durable correlation race; request scope isolation
  and canonical durable response correlation are covered by unit and functional tests.
- 2026-08-25: The distributed final-image workflow also passes with PostgreSQL 18, RustFS, two
  external workers, isolated API/worker metrics, simultaneous worker crash recovery, independent
  PostgreSQL and S3 readiness failures, and exact database/object backup and restore. Both required
  storage profiles now satisfy the T21 final-image matrix locally.
- 2026-08-25: Independent review follow-up strengthened the final-image contract. Both worker
  metrics endpoints are scraped, recovery requires exactly one reclaim, Playwright covers password
  reset plus session expiration and authenticated state across restart, standalone liveness remains
  healthy during a readiness failure, and RustFS snapshots are fully verified before replacement.
  Controlled browser failure-artifact collection is covered without retaining private error text.
- 2026-08-25: The strengthened standalone and distributed rootless-image workflows both pass.
  Canonical formatting, linting, type checking, CI validation, and web tests pass; the applicable
  Python suite passes with real PostgreSQL and RustFS (`1230 passed, 44 deselected`, 95.53% branch
  coverage). The full host suite still cannot execute the 44 Pandoc, Mermaid/Chromium, and
  LibreOffice-marked tests because those engines are unavailable on the host; both final images
  exercised all three engines successfully.
- 2026-08-25: Final independent review found no remaining blocking or non-blocking correctness
  issues after the distributed API-and-worker restart, private failure-only conversion results,
  recovery-browser diagnostics, and widened expiration boundary were revalidated in both images.
- 2026-08-25: PR #57 exact-head CI run `32801768871` passed every required domain, including both
  final-rootless-image E2E profiles. The reviewed head `12a2efe` was squash-merged as `c99fab0`, and
  exact-main run `32802323844` passed every job. The host-only document-engine limitation is fully
  covered by successful final-image and CI engine execution. All acceptance criteria are verified
  on `main`; T21 is `Done`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
