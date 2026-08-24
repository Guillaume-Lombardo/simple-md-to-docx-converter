---
ticket: T04
linear_id: G1L-314
linear_url: https://linear.app/g1lom/issue/G1L-314/
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T04 - Build the reference corpus and golden-test infrastructure

## Objective

Build the document corpus, golden infrastructure, fixtures, marker registration, and deterministic comparison tools.

## Acceptance criteria

- The implementation satisfies the T04 outcome in `docs/product-specification.md`.
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
- T01

## Progress

- Defined mandatory integration coverage for every feature that crosses a real boundary.
- Defined mandatory E2E coverage for every delivered user-visible or operational workflow.
- Defined the explicit justification and reviewer-approval process for exceptions.
- 2026-08-24: T00 and T01 are verified `Done` in both the repository and Linear. T04 implementation started on `feat/T04-golden-infrastructure` to add the approved corpus categories, deterministic DOCX/OpenXML and PDF-raster comparison primitives, reusable fixtures, marker coverage, and tests without implementing the downstream T07/T08/T09/T10/T11 conversion features themselves.
- 2026-08-24: Added the manifest-validated reference corpus with all required categories and future owners; deterministic, provenance-pinned DOCX and adversarial ZIP builders; strict path/archive/OpenXML inspection with caller-supplied preflight limits and bounded integrity-checked reads; bounded one-pass RGBA raster comparison; reusable fixtures; exhaustive unit and integration rejection coverage; active T04 document-engine CI execution; and usage documentation. T04 adds no conversion behavior or final-image workflow, so E2E coverage is not applicable. Linear remains `In Progress` until the change is verified on `main`.
- 2026-08-24: GitHub PR #32 exact head `29c2ecae0eb36d26883459c71382b0de170d7583` received independent approval with no findings after the bounded-I/O and exception-contract regressions passed, passed run `32678015088`, and was squash-merged into `main` as `c2b84478282beb1c3ce1e231d107610e8ec65b82`. That exact squash passed main run `32678078658`, including the active `CI / document-engines` job, both storage profiles, and the protected `CI / gate`. T04 is complete and T07 is unblocked; T08 still waits for T07. Linear G1L-314 transitions to `Done` after this durable completion sync reaches `main`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
