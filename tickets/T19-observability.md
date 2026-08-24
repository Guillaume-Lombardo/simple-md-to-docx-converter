---
ticket: T19
linear_id: G1L-329
linear_url: https://linear.app/g1lom/issue/G1L-329/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T19 - Add observability, audit, and traceability

## Objective

Add structured logs, correlation, metrics, queue observability, audit, version traceability, and cheap readiness.

## Acceptance criteria

- The implementation satisfies the T19 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- T15
- T18

## Progress

- 2026-08-24: Started implementation on `feat/T19-observability` from verified `main` at
  `375abd7` after T15 and T18 were confirmed `Done`. This workstream owns application structured
  logging, correlation, metrics, queue/audit/version traceability, cheap readiness, and their
  source-level tests and documentation. T20 owns final-image, container, deployment, SBOM, and
  vulnerability-scan artifacts; T19 will not edit those components.
- 2026-08-24: Implemented durable request-to-worker correlation, content-free JSON application
  events, low-cardinality operational metrics, aggregate queue gauges, bounded administrator audit
  reads, component/template version traceability, and profile-aware bounded readiness probes.
  SQLite/filesystem and PostgreSQL/RustFS success and failure behavior is covered by functional and
  integration tests. `uv sync --all-groups`, Ruff formatting/linting, `ty check`, 797 unit tests
  (93.67% coverage), and the canonical non-document-engine suite of 993 tests (95.25% coverage)
  passed. Pandoc, Chromium, and LibreOffice are unavailable locally, so the unrestricted suite was
  not run. Final-rootless-image E2E validation remains explicitly assigned to T20 and must be
  reported and independently reviewed before T19 can be marked `Done` on `main`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
