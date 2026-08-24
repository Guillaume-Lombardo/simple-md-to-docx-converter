---
ticket: T08
linear_id: G1L-318
linear_url: https://linear.app/g1lom/issue/G1L-318/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T08 - Secure ZIP handling and image processing

## Objective

Implement safe archive validation and extraction, image normalization, hostile SVG handling, and security tests.

## Acceptance criteria

- The implementation satisfies the T08 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- T04
- T07

## Progress

- 2026-08-24: T04 and T07 are verified `Done` in the repository and Linear. Implementation started
  on `feat/T08-secure-archives-images`. Scope is limited to safe ZIP validation/extraction, root
  Markdown selection, local-resource resolution, PNG/JPEG/static-GIF/WebP normalization, hostile
  SVG sanitization and local rasterization, and their security/integration tests. T09 Mermaid, T10
  template/font management, T11 PDF, and T18 production limit values remain outside this ticket;
  archive and image limits stay configurable.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
