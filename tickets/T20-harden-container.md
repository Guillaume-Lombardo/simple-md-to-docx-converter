---
ticket: T20
linear_id: G1L-330
linear_url: https://linear.app/g1lom/issue/G1L-330/
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T20 - Build and harden the final container image

## Objective

Build the reproducible rootless image with API and worker modes, SBOM, scans, and smoke tests.

## Acceptance criteria

- The implementation satisfies the T20 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.
- The final image supports both storage profiles and uses the provider-neutral AWS S3-compatible object-store contract; distributed-profile test deployments use RustFS, never MinIO.
- The hardened final image is used to discharge the rootless E2E debt explicitly deferred from T12, with explicit reviewer approval of the sequencing exception.
- The final image includes the T11 PDF adapter and locked document engines so T21 can discharge
  deferred end-to-end PDF success, failure, cancellation, concurrency, and asynchronous-workflow
  coverage.

## Dependencies

- T11
- T12
- T13
- T18

## Progress

- 2026-08-23: Scope now records the T12 sequencing debt: build the final rootless image for both profiles so T21 can verify the deferred storage workflows. RustFS is the CI/k3s S3-compatible implementation; the application contract remains provider-neutral and AWS S3-compatible.
- 2026-08-24: Scope now also records T11's approved sequencing debt. The final image must carry the
  verified Pandoc, LibreOffice, PDFium test, and font contracts needed for T21's final asynchronous
  PDF workflow E2E.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
