---
ticket: T07
linear_id: G1L-317
linear_url: https://linear.app/g1lom/issue/G1L-317/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T07 - Convert approved Markdown to DOCX

## Objective

Convert the fixed Markdown dialect to DOCX with fixed Pandoc arguments, isolated workspaces, and reference documents.

## Acceptance criteria

- The implementation satisfies the T07 outcome in `docs/product-specification.md`.
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
- T06

## Progress

- No implementation work started.
- 2026-08-24: T04 and T06 are verified `Done` in the repository and Linear. T07 implementation started on `feat/T07-pandoc-docx` to add the fixed Markdown dialect, mandatory pre-Pandoc raw-HTML rejection, an isolated-workspace Pandoc adapter with fixed arguments and an explicit environment, reference-document support, stable errors, and real Pandoc/OpenXML integration coverage. T08 archive/image security, T09 Mermaid, T10 font/template validation, T11 PDF, and T18 production limits remain outside this ticket.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
