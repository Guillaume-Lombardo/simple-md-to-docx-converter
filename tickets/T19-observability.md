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
- 2026-08-24: Second-review corrections started from `ac49563`. The bounded T19 scope is to make
  request correlation entirely server-generated, bound external-worker metrics admission and
  request execution, harden distributed-test resource isolation and failure sequencing, and make
  cross-table audit cleanup globally ordered and concurrency-safe with real PostgreSQL evidence.
  T20 ownership, publication, merge, and ticket state remain unchanged.
- 2026-08-24: Second-review corrections completed. HTTP correlation is now a fresh server UUIDv4
  for every request and caller text never reaches the response identifier, durable job row, or log.
  External-worker metrics use fixed request concurrency, a bounded accept queue, an absolute header
  deadline, a separate database-observation cap, safe saturation, and leak-free bounded shutdown.
  Every distributed integration test now owns a unique PostgreSQL schema and, when applicable, a
  unique RustFS bucket; both are removed in fixture teardown, and the missing-bucket-to-healthy
  regression passes without shared state. Audit cleanup uses one globally ordered `UNION ALL`
  candidate limit and one PostgreSQL transaction advisory lock, so cleaners serialize without
  selecting or locking `limit` rows from each table. Direct audit updates/deletes are rejected and
  only the guarded cleanup transaction may delete selected expired rows and commit immutable
  evidence. Real PostgreSQL covers merged pagination, both immutable operations, shared retention,
  concurrent cleaners, and revision 11 downgrade removal. During validation, one downgrade test
  initially counted a same-named public-schema trigger and was corrected to target its isolated
  relation. A later complete run exposed five deterministic failures from legacy direct audit
  teardown in the shared schema; per-test schema/bucket isolation removed that contamination, and
  the entire PostgreSQL directory then passed. Five repeated high-risk real-boundary runs passed
  20/20. The unit gate passes with 843 tests and 93.35% total coverage. The correct-environment
  canonical suite passes with 1,055 tests and 95.35% total coverage; its no-coverage confirmation
  also passes 1,055 tests. The unrestricted engine suite remains unavailable because Pandoc,
  Chromium, and LibreOffice are not installed. T19 remains `In Progress` pending T20/T21 final-image
  validation, independent review, merge, and verification on `main`.
- 2026-08-24: Final-review corrections completed. Queue observation now owns a finite database
  budget on a dedicated engine: PostgreSQL uses bounded pool checkout plus transaction-local
  `statement_timeout`, SQLite uses an interruptible progress deadline, and listener shutdown
  rejects new observations and cancels active driver calls before joining its fixed request pool.
  A truly blocked scrape stops within the bounded lifecycle without leaving metrics threads, and a
  real PostgreSQL table lock proves the statement timeout. Every documented OpenAPI response now
  declares the server-generated `X-Correlation-ID` UUID header. Distributed test isolation uses an
  `ExitStack` so engine disposal and schema deletion still run when S3 client creation, bucket
  creation, or bucket cleanup fails. Ruff, `ty`, and `git diff --check` pass; the unit gate passes
  with 850 tests and 93.40% total coverage, and the correct-environment canonical suite passes with
  1,064 tests and 95.43% total coverage plus a matching 1,064-test no-coverage run. The unrestricted
  document-engine suite remains unavailable locally. T19 remains `In Progress` pending T20/T21
  final-image validation, independent review, merge, and verification on `main`.
- 2026-08-24: PR #54 CI run `32781621531`, job `97604757297`, exposed a distributed isolation
  regression: the metrics endpoint returned `503` and PostgreSQL reported `relation
  conversion_jobs does not exist`. The bounded engine's `connect_args` had replaced the fixture's
  URL-level `options=-csearch_path=<unique schema>` with `statement_timeout`. Engine creation now
  preserves URL/libpq options verbatim and installs the bounded PostgreSQL statement timeout through
  a parameterized connection hook, avoiding ambiguous string composition. Unit coverage proves the
  existing search path survives unchanged, and real PostgreSQL proves both the isolated current
  schema and `500ms` timeout while queue metrics query the migrated table. The exact CI distributed
  matrix passes 28 tests; the unit gate passes 850 tests with 93.41% total coverage; and the full
  no-coverage suite passes 1,065 tests. Two full covered reruns each passed 1,064 tests with 95.38%
  coverage but retained one unrelated pre-existing wall-clock failure in
  `test_heartbeat_covers_blocked_real_result_publication` (`entered.wait(1)`); that unchanged test
  passes three no-coverage isolated reruns and one covered isolated rerun. No unrelated timing scope
  was added. T19 remains `In Progress` pending CI confirmation, independent review, merge, final
  image evidence, and verification on `main`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
