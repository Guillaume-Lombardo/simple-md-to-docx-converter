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
- 2026-08-24: Review corrections started from `07eef29`. Scope is limited to isolating bounded
  readiness adapters from normal persistence clients, completing durable authentication-mutation
  audit parity and deterministic merged reads, adding an independently scrapeable external-worker
  metrics lifecycle, and validating every structured-log value. T20 retains exclusive ownership of
  image/runtime/deployment files; T19 will publish the source-level worker exporter contract only.
- 2026-08-24: Final-image sequencing is an exception to execution order, not an E2E waiver. T20
  must verify standalone and distributed rootless images for isolated readiness success/failure,
  API metrics, account audit success/authorization failure, and a concurrently scrapeable external
  worker metrics listener with distinct API/worker counters and clean lifecycle. T21 must exercise
  both published profile deployments, scrape every API/worker process, and prove merged template
  and authentication audit ordering/retention survives backup and restore. Independent review of
  those results is required before this ticket can become `Done`.
- 2026-08-24: Review corrections completed. Readiness now uses dedicated bounded PostgreSQL and S3
  clients, authentication mutations write immutable same-transaction audit rows in both profiles,
  the administrator audit feed merges authentication and template rows with global deterministic
  pagination/retention, external workers expose process-local metrics through a managed HTTP
  listener, and every structured-log value is validated against bounded canonical forms. The first
  distributed coverage attempt used the wrong RustFS bucket (`md-converter-tests` instead of the
  provisioned `md-converter-test`) and failed as expected. The immediately following corrected run
  reported failures in the missing-bucket and PostgreSQL authentication-contract cases after that
  contaminated run; neither reproduced in ten consecutive paired reruns, the complete covered suite
  then passed with 1,023 tests and 95.28% total coverage, and the complete no-coverage suite passed
  with 1,023 tests. `uv sync --all-groups`, Ruff formatting/linting, `ty check`, and `git diff
  --check` pass. The unrestricted suite remains unavailable because Pandoc, Chromium, and
  LibreOffice are not installed. T19 remains `In Progress` pending T20/T21 final-image validation,
  independent review, merge, and verification on `main`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
