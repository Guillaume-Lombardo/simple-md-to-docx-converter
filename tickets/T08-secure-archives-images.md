---
ticket: T08
linear_id: G1L-318
linear_url: https://linear.app/g1lom/issue/G1L-318/
status: Done
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
- 2026-08-24: Implementation is complete and independently approved on commit `174c04e`. ZIP
  preflight and bounded reads reject unsafe paths, collisions, special files, encryption,
  unsupported compression, excessive ratios/counts/sizes, CRC failures, and invalid UTF-8. Local
  PNG/JPEG/static-GIF/WebP/SVG resources are normalized to deterministic PNG; SVG XML, CSS, tree
  depth, element count, and Cairo rasterization are constrained. Markdown references are bound to
  the immutable package manifest, whose normalized PNG bytes and carried image limits are
  revalidated before isolated Pandoc materialization.
- 2026-08-24: Exact-head verification passed: Ruff format/lint, `ty`, lock and CI validation; 383
  unit tests with 90.52% application branch coverage; 92.97% changed-line coverage; 509 canonical
  host tests with PostgreSQL and RustFS (95.82% total coverage); and 90 document-engine integration
  tests in the T00 UBI image under an arbitrary UID with a read-only root filesystem. Temporary
  containers were removed and K3s remained inactive. A reviewer explicitly approved deferring
  final-image E2E to T20/T21 because T08 delivers an internal synchronous component rather than a
  user-visible or operational workflow.
- 2026-08-24: PR #36 was squash-merged as `ca46aabe4864b8b1dc5e85a03f584abc834d2dce`.
  Exact-main CI run `32705438253` passed light checks, CI infrastructure, functional,
  document-engine, standalone-storage, distributed-storage, and the protected gate. T08 is verified
  on `main` and synchronized as `Done`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
