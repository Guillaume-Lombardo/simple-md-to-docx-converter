---
ticket: T18
linear_id: G1L-328
linear_url: https://linear.app/g1lom/issue/G1L-328/
status: Done
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
- 2026-08-24: Applied the four approved PM decisions. Superseded template versions use leased,
  fenced, object-first cleanup after 365 days while protecting the current version, ten newest
  versions, and retained-job references. Audit records are database-immutable for 365 days and
  bounded deletion retains immutable cleanup evidence. ClamAV INSTREAM scans every conversion and
  template upload before validation, reservation, or object persistence; infected content is
  rejected, scanner uncertainty fails closed, and no quarantine or scan temporary file is kept.
  Recovery policy fixes standalone RPO 24h/RTO 4h and distributed RPO 1h/RTO 2h, with an automated
  isolated restore runner and immutable retained quarterly report.
- 2026-08-24: Added deterministic unit, real clamd TCP protocol, SQLite/filesystem, and live
  PostgreSQL/RustFS profile-parity coverage for primary and failure paths. Targeted validation is
  green. Final validation passed Ruff formatting/linting, `ty`, all 22 Web tests, the 781-test unit
  suite with its independent 90% branch gate, and the 962-test applicable canonical suite with live
  PostgreSQL/RustFS and 94.99% displayed application coverage. The unrestricted suite reached 966
  passing tests; its 37 failures are confined to unavailable Pandoc, Mermaid/Chromium,
  LibreOffice, and font artifacts. A real ClamAV installation is unavailable on this host, so the
  deterministic real TCP INSTREAM boundary test is the local provider proof. Changed application
  line coverage was 98.36% (601/611 lines) for the initial implementation revision.
- 2026-08-24: Closed final-review gaps by assembling the same retention service into production
  external and embedded worker factories, advancing cleanup cadence before transient-error
  backoff, and adding assembled behavior coverage. ClamAV now accepts only one canonical,
  NUL-terminated `stream: OK` or valid `stream: <signature> FOUND` record and fails closed for empty
  prefixes, malformed termination, multiple or contradictory records, and trailing data. Alembic
  revision `20260824_09` prevents both update and deletion of cleanup evidence while leaving the
  approved bounded deletion of expired audit rows intact; real SQLite and isolated PostgreSQL
  upgrade/downgrade tests verify the trigger lifecycle without weakening production enforcement.
  Restore exercises now measure RTO from an injected monotonic clock while retaining UTC evidence,
  including wall-clock rollback coverage. Final review validation passed 62 focused tests, Ruff,
  `ty`, all 22 Web tests, 786 unit tests with the branch gate, and 978 applicable tests with live
  PostgreSQL/RustFS and 95.06% application coverage. Changed application coverage is 98.67%
  (669/678 lines) at commit `5f7e6b3`.
- 2026-08-24: Diagnosed the two failures in pull-request CI run `32768243621`. The standalone
  SQLite heartbeat failure was a timing race in an 80 ms test lease, not a production lease-loss
  defect; the integration test now uses a thread-safe logical clock and observes a real database
  heartbeat before testing recovery past the original lease. The administration browser failure
  was deterministic harness drift after ClamAV integration: without clamd, the harness returned
  `UPLOAD_SCANNER_UNAVAILABLE` before invalid-template validation. The browser harness now injects
  the explicit trusting test scanner and asserts the exact 422 response and
  `TEMPLATE_INVALID_PACKAGE` code before checking the rendered alert. No blanket timeout was
  increased. The heartbeat regression passed 30 consecutive runs; the full browser suite passed
  six consecutive runs with pinned Chrome 151.0.7922.173 (SHA-256
  `878e5ab495b8a694980fca61bc09b37e651ccedce2291c73434d16e48a2646fd`). Final local validation
  passed the 31-test standalone storage domain, Ruff formatting/linting, `ty`, 786 unit tests, and
  978 applicable tests with live PostgreSQL/RustFS and 95.06% application coverage.
- 2026-08-24: Independently approved head `9b600da` passed exact-head CI run 32769890696 across
  light, functional, standalone, distributed, document-engine, authenticated browser, and final
  gate stages. Ready pull request
  [#52](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/52) was squash-merged
  as `747acf2`; the exact implementation branch was confirmed absent remotely and its local branch
  and worktree were removed. Exact-main CI run 32770246087 then passed the same complete matrix.
  Final rootless-image application of memory/ephemeral-storage limits and both-profile E2E remain
  mandatory T20/T21 sequencing work under the independently approved exception.

Workload-dependent ceilings and schedules remain required operator-supplied configuration. Applying memory and ephemeral-storage ceilings and
repeating the quota workflows against the final rootless image remain T20/T21 sequencing debt, not
a waiver.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
