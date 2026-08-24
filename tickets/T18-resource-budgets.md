---
ticket: T18
linear_id: G1L-328
linear_url: https://linear.app/g1lom/issue/G1L-328/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T18 - Add quotas, limits, and resource budgets

## Objective

Add quotas, queue capacity, resource budgets, retention, periodic cleanup, cancellation, and short load tests.

## Acceptance criteria

- The implementation satisfies the T18 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- T00
- T12
- T13

## Progress

- 2026-08-24: Started implementation on `feat/T18-resource-budgets` from `main` at `6c222ec`
  after confirming Linear and repository scope, acceptance criteria, and completed T00/T12/T13
  dependencies. This workstream owns configurable production quotas, queue capacity, resource
  budgets, retention, periodic cleanup, cancellation behavior, and short load tests across both
  storage profiles. T17 administration UI and T19 observability remain excluded.
- 2026-08-24: Implemented required configuration and typed policies for document ceilings, atomic
  per-user/global active-work admission, worker duration/memory/ephemeral-storage budgets, lease/recovery
  timings, retained job artifacts, and elapsed-time cleanup. SQLite serializes admission with its
  write transaction; PostgreSQL uses a transaction-scoped advisory lock. Exact idempotent replays
  bypass later saturation. Duration exhaustion produces a safe stable failure while durable user
  cancellation retains precedence. Cleanup remains bounded, fenced, retry-safe, and deterministic.
- 2026-08-24: Added unit, SQLite/filesystem, PostgreSQL/RustFS, and short concurrent load coverage.
  The applicable canonical suite passed with 907 tests and 95% displayed application line coverage;
  real T18 PostgreSQL/RustFS tests passed. The unrestricted suite reached 906 passing tests but 37
  document-engine tests failed because Pandoc, Mermaid/Chromium, and LibreOffice are unavailable on
  this host. Post-T17 assembly must wire the new repository/policy factory and map owner saturation
  to HTTP 429 and global saturation to HTTP 503 with `Retry-After`. Final-image cgroup/ephemeral
  enforcement and E2E remain T20/T21 work. No production values, antivirus provider, template/audit
  retention contract, RPO, or RTO were invented without PM approval.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
