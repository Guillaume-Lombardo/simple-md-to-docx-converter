---
ticket: T12
linear_id: G1L-324
linear_url: https://linear.app/g1lom/issue/G1L-324/
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T12 - Implement both storage profiles

## Objective

Implement repository and object-store abstractions, both storage profiles, Alembic migrations, atomic files, and contract tests.

## Acceptance criteria

- The implementation satisfies the T12 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.
- The distributed object-store adapter is provider-neutral and AWS S3-compatible; CI and k3s use RustFS, never MinIO.
- Shared contract tests cover standalone atomic files and the distributed object-store implementation.
- Real PostgreSQL and RustFS integration tests cover the primary successful path and every relevant failure behavior.
- Final-image rootless E2E is deferred only to T20/T21, is not a waiver of T12 integration coverage, and requires explicit pull-request justification and reviewer approval.
- RPO/RTO, retention, quotas, antivirus, and cleanup values remain configurable and unresolved until separately approved.

## Dependencies

- T05
- T06

## Progress

- 2026-08-23: PM selected RustFS for CI/k3s while retaining a provider-neutral AWS S3-compatible contract. Real PostgreSQL/RustFS integration success and relevant failures plus shared contract tests remain in T12; only final-image rootless E2E is deferred to T20/T21 with explicit reviewer approval.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
