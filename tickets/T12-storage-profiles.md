---
ticket: T12
linear_id: G1L-324
linear_url: https://linear.app/g1lom/issue/G1L-324/
status: In Progress
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
- 2026-08-23: Started implementation on `feat/T12-storage-profiles` from `main` at `33f86a0` after confirming Linear project, team, priority, scope, acceptance criteria, and dependency parity. T05 and T06 are both `Done`; T12 has no remaining dependency blocker.
- 2026-08-23: Implemented a fail-fast standalone/distributed profile discriminator, Alembic-managed authentication schema, transactionally atomic SQLite/PostgreSQL user and session repositories, PostgreSQL migration locking, cheap database/object readiness, atomic filesystem objects under `/data`, and a provider-neutral AWS S3-compatible adapter. Object keys contain only fixed namespaces and stable UUIDs.
- 2026-08-23: Added shared repository and object-store contracts plus real SQLite/filesystem, PostgreSQL 18.6, and pinned RustFS 1.0.0-beta.12 integration suites. Coverage includes successful persistence and restart, administrator no-reset behavior, stale login CAS rejection, concurrent security-version increments, durable session mutation/revocation, atomic overwrite and cleanup, missing objects, missing buckets, and sanitized storage failures. The standalone and distributed CI domains are active with pinned PostgreSQL/RustFS services; MinIO is not used.
- 2026-08-23: `uv sync --all-groups`, Ruff format/lint, `ty`, both 161-test canonical Pytest commands, exact storage domain commands, `uv lock --check`, repository CI validation, checksum-verified actionlint v1.7.12, hash-constrained sdist/wheel builds, and `git diff --check` pass. Full application coverage is 97.77%; the light suite reaches 94.90%, the independent branch threshold passes, and changed executable application lines reach 90.17%. The only warning is Starlette's existing non-blocking TestClient/httpx2 deprecation notice.
- 2026-08-23: Documented coherent configuration and coordinated backup/restore mechanics without fixing RPO/RTO, retention, quota, antivirus, or cleanup values. T13 queue and T14/T15 template workflows are not pre-implemented. Final hardened-image rootless E2E remains the approved T20/T21 sequencing debt and requires explicit reviewer approval in the pull request; T12 real integration coverage is complete.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
