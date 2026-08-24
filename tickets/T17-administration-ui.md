---
ticket: T17
linear_id: G1L-326
linear_url: https://linear.app/g1lom/issue/G1L-326/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T17 - Build template and account administration UI

## Objective

Build owner and administrator template management and local-account management with multi-user browser tests.

## Acceptance criteria

- The implementation satisfies the T17 outcome in `docs/product-specification.md`.
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
- T16

## Progress

- 2026-08-24: Started implementation on `feat/T17-administration-ui` from `main` at `6c222ec`
  after confirming Linear and repository scope, acceptance criteria, and completed T15/T16
  dependencies. This workstream owns the template and administrator-account browser interfaces,
  their dedicated static assets, HTTP/UI adapters, and multi-user browser/functional tests. T18
  production resource policy and T20/T21 final-image E2E remain excluded.
- 2026-08-24: Implemented the authenticated owner/administrator template page, complete versioned
  lifecycle controls, preferences, safe owner display, and the administrator local-account tab.
  Added server-enforced two-user/administrator functional coverage, native JavaScript coverage
  gates, and a pinned-Chromium scenario over the real standalone persistence and filesystem
  boundaries. T20/T21 remain responsible for repeating these workflows against the hardened final
  image and both deployable profiles; this sequencing debt is not a waiver of final E2E coverage.
- 2026-08-24: Verified Ruff formatting/lint, `ty`, native JavaScript tests, both pinned-Chromium
  browser scenarios, and 860 locally runnable Python tests. Application coverage is 94.52%; the
  T17 JavaScript module has 100% line/function and 91.27% branch coverage. Host document engines
  remain unavailable, while the available rootless toolchain image passed 34 of 44 engine tests but
  carries stale font/golden evidence. Final hardened-image and two-profile E2E remain T20/T21 debt.
- 2026-08-24: Review hardening added abort/generation guards for late template, account, and version
  responses; duplicate-submit guards; malformed-success handling; and browser coverage for invalid
  DOCX, CSRF denial, stale `If-Match`, guarded deletion, revoked sessions, and duplicate creation.
  Live PostgreSQL/RustFS integration now verifies owner representation and search with two users and
  an administrator, persistence across clients, authorization denial, and sanitized missing-bucket
  and database failures without partial catalog state or leaked details. The browser documentation
  now distinguishes loopback HTTP integration from the deployment HTTPS, rootless-runtime, and
  final-image E2E work owned by T20/T21.
- 2026-08-24: Reverified the hardened change: Ruff format/check and `ty` pass; 22 native JavaScript
  tests pass with 100% lines, 98.11% functions, and 92.11% branches for the administration module;
  both pinned-Chromium scenarios pass; and the canonical non-engine Python suite passes 877 tests
  with 94.68% independently reproducible application coverage, including live PostgreSQL/RustFS
  tests. The unfiltered suite still has 37 expected host failures because Pandoc,
  Mermaid/Chromium, LibreOffice, and the pinned font inventory are not installed on the host.
  Final-image engine and both-profile E2E verification remain T20/T21 sequencing debt.
- 2026-08-24: Corrected PR CI evidence by adding unit coverage for the authenticated administration
  page, its immutable assets, and its unauthenticated redirect. The exact light selection now passes
  710 tests with 90.09% application branch coverage and 100% changed application coverage (28/28
  executable lines) against `6c222ec`. The pinned-Chromium account workflow now waits for the
  re-rendered, enabled `Reactivate` control before clicking it; this removes the lost-click race
  without increasing timeouts. The administration scenario passed three consecutive pinned-browser
  runs, followed by the complete two-scenario browser suite.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
