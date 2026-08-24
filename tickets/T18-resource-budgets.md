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
  The corrected applicable canonical suite passed with 918 tests and 95% displayed application
  line coverage; real T18 PostgreSQL/RustFS tests passed. A separate unrestricted run without
  service environment variables reached 909 passing tests and 53 expected failures because
  Pandoc, Mermaid/Chromium, LibreOffice, PostgreSQL, and RustFS are unavailable in that invocation.
  Final-image cgroup/ephemeral enforcement and E2E remain T20/T21 work. No implicit production
  values, antivirus provider, template/audit retention contract, RPO, or RTO were invented.
- 2026-08-24: Independent-review corrections switched overall job duration to a monotonic execution
  budget carried by the processor cancellation probe, fixed deterministic lease/cancellation/
  duration/error precedence, removed the unsupported upload/decompression ordering, added typed
  archive/diagram policy projections, completed local/profile configuration placeholders, and
  expanded both real storage-profile suites for same-owner races, saturated exact replay,
  idempotency conflict, result-attempt cleanup, injected deletion failure, cleanup-lease reclaim,
  and stale acknowledgement fencing.
- 2026-08-24: Merged green T17 `main` at `0f6d4d5` without rewriting history and completed production
  API assembly. Both profiles now construct the real repository with the configured atomic
  admission policy; owner saturation returns stable HTTP 429 and global saturation returns stable
  HTTP 503, both with `Retry-After`. Exact saturated replays remain accepted. Shared archive and
  Mermaid ceilings constrain their processor adapters, and the worker's monotonic deadline is
  passed through frozen-template resolution so Pandoc, Mermaid, and LibreOffice cap each engine
  invocation by the remaining job duration. Real ASGI quota tests cover SQLite/filesystem and
  PostgreSQL/RustFS.
- 2026-08-24: Final assembly validation passed Ruff formatting/linting, `ty`, all 22 Web tests,
  763 unit tests, 219 focused API/policy/engine/SQLite tests, all 3 focused T18 PostgreSQL/RustFS
  tests, and the 941-test applicable canonical suite with real PostgreSQL/RustFS. The unrestricted
  suite reached 948 passing tests; its 37 failures are confined to unavailable
  Pandoc/Mermaid/Chromium/LibreOffice/font-engine tests.

## Unresolved PM decisions

1. Template-version retention duration and deletion semantics.
2. Audit-record retention duration and deletion semantics.
3. Antivirus provider, scan boundary, failure policy, and quarantine behavior.
4. Standalone and distributed RPO/RTO targets and their required operational proof.

All implemented numeric ceilings and schedules remain required operator-supplied configuration
without repository production defaults. Applying memory and ephemeral-storage ceilings and
repeating the quota workflows against the final rootless image remain T20/T21 sequencing debt, not
a waiver.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
