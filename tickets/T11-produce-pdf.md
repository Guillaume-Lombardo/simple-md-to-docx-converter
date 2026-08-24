---
ticket: T11
linear_id: G1L-320
linear_url: https://linear.app/g1lom/issue/G1L-320/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T11 - Produce PDF with isolated LibreOffice

## Objective

Produce PDF with isolated LibreOffice profiles, traceability metadata, timeout and cancellation, and golden tests.

## Acceptance criteria

- The implementation satisfies the T11 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- T09
- T10

## Progress

- 2026-08-24: T09 and T10 are verified `Done` in the repository and Linear. Implementation started
  on `feat/T11-pdf-conversion`. Scope is deterministic DOCX-to-PDF conversion through an isolated
  LibreOffice profile, bounded shell-free execution with whole-group timeout/cancellation,
  reproducibility metadata, structural/raster golden validation, and real rootless engine failure
  coverage. T13 owns asynchronous job-state cancellation wiring; T20/T21 retain final-image E2E.
- 2026-08-24: Implemented the isolated, shell-free LibreOffice adapter with explicit DOCX/PDF
  archive and structure limits, whole-process-group timeout/cancellation, fail-closed output reads,
  strict PDF parsing, safe canonical traceability, and stable error categories. Added locked pypdf
  and PDFium dependencies, deterministic reference normalization, a reproducible PNG golden, and
  unit/real-engine failure coverage. The focused suite passes 85 local tests; the rootless UBI
  harness passes 18 real Pandoc/LibreOffice/PDFium tests under an arbitrary UID, read-only root,
  no network, no capabilities, and bounded resources. Canonical full-suite validation and
  independent review remain before publication.
- 2026-08-24: Resolved independent security, specification, and CI review findings: cancellation
  probe failures now terminate the whole process group; final waits and decoded streams are
  bounded; all standard unsafe PDF actions and parser failures are normalized; the golden verifies
  engine, font, provenance, dimensions, and deterministic uncompressed reference bytes; and a
  committed rootless harness reproduces the real boundary suite. T20/T21 and Linear now retain the
  explicitly approved final-image asynchronous E2E debt. The final security pass also rejects
  unknown typeless actions and non-HTTP(S) URI actions, normalizes malformed page trees, and bounds
  the executable rootless workspace to a 512 MiB tmpfs. Formatting, Ruff, and `ty` pass; 608 unit
  tests pass at 93.97% coverage, 749 default local tests pass at 95.02%, and all 20 rootless real-
  engine tests pass. The unfiltered host run has 34 expected missing-engine failures; those exact
  boundaries pass in the approved image. Exact-revision re-review and changed-line coverage remain
  before publication.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
