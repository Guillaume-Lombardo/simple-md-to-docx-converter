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

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
