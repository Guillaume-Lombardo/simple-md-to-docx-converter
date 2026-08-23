---
ticket: T01
linear_id: G1L-311
linear_url: https://linear.app/g1lom/issue/G1L-311/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T01 - Initialize the repository and developer workflow

## Objective

Initialize the English repository, uv project, architecture, canonical commands, contribution rules, and local developer workflow.

## Acceptance criteria

- The implementation satisfies the T01 outcome in `docs/product-specification.md`.
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

## Progress

- Created the Linear project and T00–T23 issues with dependency relationships.
- Added repository ticket mirrors and the Linear synchronization skill.
- Added repository-wide synchronization rules to `AGENTS.md` and the product specification.
- Delivered the synchronization foundation through GitHub PR #2 and verified it on `main` at `6b5a30e5a45ad8634cf02115a978173e4a93428f`.
- Implemented the remaining bootstrap on `chore/T01-bootstrap-repository`: Python 3.14, the `md-converter` distribution and `md_converter` package, locked `uv` environments, hash-constrained reproducible builds, required Pytest marker registration, a deterministic package-metadata test, and English repository, architecture, contribution, and local-development documentation.
- Addressed independent review by constraining Hatchling and its transitive build dependencies, inspecting clean-cache wheel and source distributions, limiting the source distribution to publishable content, and leaving Ruff lint policy to T05.
- Verified every canonical command, `uv lock --check`, a clean locked no-cache synchronization, a hash-required no-cache build, fresh-wheel import and metadata, 100% branch coverage of the application package, and `git diff --check`.
- Integration and final-image E2E coverage are not applicable to this bootstrap because it introduces no real component boundary and no user-visible or operational workflow; no integration or E2E exception is claimed, and the pull-request reviewer must confirm this applicability assessment.
- T01 remains In Progress until this bootstrap is merged and verified on `main` and its T00 dependency is resolved. T00 remains In Progress pending approved engine-source and Chrome/OpenShift sandbox decisions plus Podman/OpenShift validation; T01 does not resolve or validate those deferred product and runtime decisions.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
