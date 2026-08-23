---
ticket: T01
linear_id: G1L-311
linear_url: https://linear.app/g1lom/issue/G1L-311/
status: Done
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

- None

## Progress

- Created the Linear project and T00–T23 issues with dependency relationships.
- Added repository ticket mirrors and the Linear synchronization skill.
- Added repository-wide synchronization rules to `AGENTS.md` and the product specification.
- Delivered the synchronization foundation through GitHub PR #2 and verified it on `main` at `6b5a30e5a45ad8634cf02115a978173e4a93428f`.
- Implemented the remaining bootstrap on `chore/T01-bootstrap-repository`: Python 3.14, the `md-converter` distribution and `md_converter` package, locked `uv` environments, hash-constrained reproducible builds, required Pytest marker registration, a deterministic package-metadata test, and English repository, architecture, contribution, and local-development documentation.
- Addressed independent review by constraining Hatchling and its transitive build dependencies, inspecting clean-cache wheel and source distributions, limiting the source distribution to publishable content, and leaving Ruff lint policy to T05.
- Verified every canonical command, `uv lock --check`, a clean locked no-cache synchronization, a hash-required no-cache build, fresh-wheel import and metadata, 100% branch coverage of the application package, and `git diff --check`.
- Integration and final-image E2E coverage are not applicable to this bootstrap because it introduces no real component boundary and no user-visible or operational workflow; no integration or E2E exception is claimed, and the pull-request reviewer must confirm this applicability assessment.
- 2026-08-23: GitHub PR #5 was independently reviewed, squash-merged into `main` as `99a56d22087211cd2b8d9bfb63b93ecb38e4768e`, and the bootstrap was verified on `main`.
- 2026-08-23: The project manager approved removing T00 as a dependency because T01's bootstrap is fully delivered and the remaining T00 engine-supply-chain, Chrome sandbox, and OpenShift decisions do not affect the repository bootstrap.
- 2026-08-23: GitHub PR #8 was independently reviewed, squash-merged into `main` as `af4cc26f304226e4dfdbe88115e08ab347f71ea0`, and the dependency removal was verified on `main`; T01 retains no dependency and continues to block T02–T06.
- 2026-08-23: Final verification on `main` at `af4cc26f304226e4dfdbe88115e08ab347f71ea0` passed `uv sync --all-groups`, `uv run ruff format .` with 38 files unchanged, `uv run ruff check .`, `uv run ty check`, `uv run pytest -m "not requires_pandoc and not requires_mermaid and not requires_libreoffice"` with 1 test passed, `uv run pytest` with 1 test passed, `uv lock --check`, and `git diff --check`.
- 2026-08-23: T01 has no remaining limitations and all acceptance criteria are satisfied. Integration and final-image E2E coverage remain not applicable because T01 introduces no real component boundary and no user-visible or operational workflow. T00 continues independently for its engine-supply-chain, Chrome sandbox, and OpenShift work and continues to block only its genuinely dependent tickets.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
