---
ticket: T15
linear_id: G1L-323
linear_url: https://linear.app/g1lom/issue/G1L-323/
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T15 - Implement the versioned template API

## Objective

Implement downloads, ETag and If-Match, atomic replacement, copy-forward restoration, audit, and profile parity.

## Acceptance criteria

- The implementation satisfies the T15 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- T10
- T14

## Progress

- No implementation work started.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
