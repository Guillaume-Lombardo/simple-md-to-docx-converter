---
ticket: T16
linear_id: G1L-327
linear_url: https://linear.app/g1lom/issue/G1L-327/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T16 - Build the asynchronous conversion UI

## Objective

Build template search, job submission, progressive polling, cancellation, expiration, and accessible downloads and errors.

## Acceptance criteria

- The implementation satisfies the T16 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- T13
- T14
- T15

## Progress

- 2026-08-24: T13, T14, and T15 are verified `Done` locally and in Linear. Implementation started
  on `feat/T16-conversion-ui` from delivered main `1635c17`. Scope is the authenticated,
  server-rendered conversion page with accessible upload/drag-and-drop, template search and
  selection, output choice, job submission, progressive status polling, cancellation, expiration,
  download, and stable English errors. T17 retains template/account administration UI, while
  T20/T21 retain final-application-image browser E2E execution.
- 2026-08-24: Implemented the authenticated `/convert` page with external native JavaScript and
  CSS, strict browser security headers, a session-bound `__Host-` CSRF cookie, accessible file
  choice and drag-and-drop, preferred/system-fallback visibility, active-template search,
  DOCX/PDF/both choice, stable idempotency reuse, progressive polling with transient recovery,
  safe step/progress/failure/expiration states, recent-job reopening, cancellation, and guarded
  downloads. T17 administration controls are absent and T18 production limits remain configurable.
- 2026-08-24: Added rendering and HTTP unit coverage, native JavaScript behavior tests with blocking
  coverage gates, a real authentication/SQLite/filesystem functional workflow, and a live
  PostgreSQL/RustFS integration workflow with isolated cleanup. The pinned Node 22.23.1 gate passes
  5 tests at 100.00% lines, 96.40% branches, and 96.55% functions. The applicable host suite passes
  866 tests at 94.62% application coverage with the existing PostgreSQL and RustFS containers.
  The unfiltered host suite passes 873 tests and has the same 37 expected engine-marked failures
  because Pandoc, Mermaid/Chromium, LibreOffice, and locked fonts are absent from the host.
- 2026-08-24: Final hardened-image Playwright E2E remains sequenced to T20/T21 because T20 has not
  yet delivered that image. T21 must repeat both-profile submission, polling, cancellation,
  expiration, download, authorization, recovery, and concurrency paths. This sequencing exception
  requires explicit pull-request reviewer approval and is not a waiver. Linear was not mutated in
  this implementation handoff as explicitly directed; the local mirror remains `In Progress` until
  verification on `main`.
- 2026-08-24: Review hardening fences template searches, submissions, polling, and cancellation so
  late responses cannot replace newer UI state; assigns an accepted job before cancellation can be
  requested; retains idempotency keys only across ambiguous transport failures; resets them after a
  confirmed response or request-changing input; and continues polling queued/running cancellation
  requests through a terminal state. Nine native JavaScript tests pass the pinned Node 22.23.1
  coverage gates at 100.00% lines, 91.03% branches, and 97.06% functions.
- 2026-08-24: Added a real-browser integration workflow using the already pinned Puppeteer and
  Chrome 151 toolchain with no runtime download or sandbox-disable flag. In the rootless T00 runtime
  it passes authenticated cookie/CSRF and CSP behavior, external script loading, keyboard search,
  file choice and drag/drop, all output choices, response-stage network ambiguity and idempotent
  retry, polling backoff, queued/running cancellation to terminal cancellation, expiration, safe
  download headers, and accessible errors. CI runs it in the existing document-engine environment.
  Linear remains intentionally untouched as directed.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
