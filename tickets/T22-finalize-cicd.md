---
ticket: T22
linear_id: G1L-332
linear_url: https://linear.app/g1lom/issue/G1L-332/
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T22 - Finalize CI/CD and release publication

## Objective

Finalize selective CI/CD, scheduled full suite, mutation testing, dependency updates, release image, SBOM, provenance, and secure publication of the `md-converter` Python distribution to PyPI.

## Scope

- Complete the selective GitHub Actions delivery workflows, scheduled full suite, targeted mutation testing, grouped dependency updates, release image, SBOM, and provenance.
- Add an isolated Python release workflow that builds the sdist and wheel once from the reviewed tagged source, verifies them, and publishes those exact artifacts to PyPI.
- Use PyPI Trusted Publishing with GitHub OIDC and a protected GitHub `pypi` environment instead of a long-lived PyPI token.
- Keep the release version and tag-trigger policy as an explicit T22 decision to document before implementation.

## Acceptance criteria

- The implementation satisfies the T22 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.
- The `md-converter` sdist and wheel are built exactly once from the reviewed tagged source.
- Distribution metadata, installation, the documented public import, and artifact integrity are validated before publication.
- The publication job publishes the exact artifacts that passed validation and never rebuilds them.
- PyPI publication uses Trusted Publishing through GitHub OIDC and the protected GitHub `pypi` environment; no long-lived PyPI token is created or stored.
- Only the publication job receives the least-privilege `id-token: write` permission, and every action is pinned by full commit SHA.
- PyPI publish attestations are generated and uploaded with the release artifacts.
- Pull requests, forks, and every other untrusted context are prevented from publishing.
- The release version and tag-trigger policy are documented and approved before the workflow is implemented.
- Before the first public release, the project manager decides the public package license and completes the one-time PyPI project and trusted-publisher configuration.

## Dependencies

- T03
- T21

## Progress

- Planning scope expanded to include secure PyPI publication in T22; no CI implementation has started.
- The official PyPI project and JSON endpoints for `md-converter` returned HTTP 404 on August 23, 2026. This is an availability observation, not a permanent reservation; no PyPI project was created or published.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
