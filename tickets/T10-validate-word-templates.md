---
ticket: T10
linear_id: G1L-321
linear_url: https://linear.app/g1lom/issue/G1L-321/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T10 - Manage fonts and validate Word templates

## Objective

Inventory fonts and licenses; validate OOXML, templates, required styles, substitutions, and canonical conversion.

## Acceptance criteria

- The implementation satisfies the T10 outcome in `docs/product-specification.md`.
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
- T07

## Progress

- 2026-08-24: T00 and T07 are verified `Done` in the repository and Linear. Implementation started
  on `feat/T10-template-font-validation`. Scope is exact official font artifacts and notices,
  deterministic Fontconfig substitutions, expected-font declarations, bounded OOXML template
  validation, required Pandoc styles, macros and external-relationship rejection, blank canonical
  Pandoc conversion, and real LibreOffice opening. T11 PDF and T15 versioned HTTP workflows remain
  downstream; T20/T21 retain final-image E2E.
- 2026-08-24: Implemented checksum-pinned Liberation 2.1.5, Carlito 1.104, Caladea 1.001, and
  DejaVu 2.37 artifacts with license evidence, an isolated deterministic Fontconfig contract, and
  an explicit Latin/Greek inventory with no Noto dependency. Implemented bounded OOXML package,
  relationship, active-content, required-style, and expected-font validation plus shell-free,
  time-bounded Pandoc and LibreOffice activation checks. Added 116 unit tests and 18 rootless
  real-engine integration tests. The unit suite passes 546 tests at 93.74% application coverage;
  the final T10 rootless toolchain image passes the T00 document-engine harness and all 18 T10
  integration tests. Canonical checks, independent review, and CI publication remain in progress.
- 2026-08-24: Independent security and specification review reproduced process-descendant,
  output-file TOCTOU, default content-type, orphan relationship, and DrawingML supplementary-font
  bypasses before publication. The corrected revision closes all five plus adjacent OLE, embedded
  package, and control-properties content-type variants with unit regressions and a real rootless
  descendant-termination proof; corrected-revision re-review is in progress.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
