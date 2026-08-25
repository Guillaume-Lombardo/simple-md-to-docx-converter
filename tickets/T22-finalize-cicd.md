---
ticket: T22
linear_id: G1L-332
linear_url: https://linear.app/g1lom/issue/G1L-332/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T22 - Finalize CI/CD and release publication

## Objective

Finalize selective CI/CD, scheduled full suite, mutation testing, dependency updates, release image, SBOM, provenance, and secure publication of the `md-converter` Python distribution to PyPI.

## Scope

- Complete the selective GitHub Actions delivery workflows, scheduled full suite, targeted mutation testing, grouped dependency updates, release image, SBOM, and provenance.
- Add an isolated Python release workflow that builds the sdist and wheel once from the reviewed tagged source, verifies them, and publishes those exact artifacts to PyPI.
- Use PyPI Trusted Publishing with GitHub OIDC, a protected GitHub `pypi` environment, and a pending Trusted Publisher for the first release instead of a long-lived PyPI token.
- Require manual approval for every publication run from designated trusted maintainers or the project manager configured as required reviewers on the `pypi` environment.
- Keep the release-version and tag-trigger policies as explicit T22 decisions to document before implementation.

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
- Every PyPI publication run waits for manual approval from designated trusted maintainers or the project manager configured as required reviewers on the `pypi` environment.
- Only the minimal PyPI upload job in the isolated workflow receives `id-token: write` for publication. A separate provenance or attestation job owned by T22 may receive `id-token: write` only when the need is documented and all its other permissions are least privilege.
- Every action is pinned by full commit SHA.
- PyPI publish attestations are generated and uploaded with the release artifacts.
- Pull requests, forks, and every other untrusted context are prevented from publishing.
- The release-version and tag-trigger policies are documented and approved before the workflow is implemented.
- Before the first public release, the project manager decides the public package license and configures a PyPI pending Trusted Publisher for the exact GitHub repository, workflow, and `pypi` environment.
- The availability of `md-converter` is rechecked immediately before the first publication attempt, which stops if the name is no longer available; a pending Trusted Publisher does not reserve the name.
- The first successful OIDC upload creates the PyPI project and converts the pending publisher into a normal Trusted Publisher, and both the project and publisher state are verified afterward.

## Dependencies

- T03
- T21

## Progress

- Planning scope expanded to include secure PyPI publication in T22; no CI implementation has started.
- Independent planning review clarified mandatory environment approval, pending Trusted Publisher bootstrap, first-upload verification, and least-privilege OIDC boundaries.
- The official PyPI project and JSON endpoints for `md-converter` returned HTTP 404 on August 23, 2026. This is an availability observation, not a permanent reservation; no PyPI project was created or published.
- 2026-08-25: Started implementation on `feat/T22-release-pipeline` from verified `main` at
  `2c01d4b` after T03 and T21 were confirmed `Done`. Work begins with the implementable CI,
  packaging-validation, dependency-update, release-artifact, SBOM, provenance, and security
  contracts. The workflow will not guess the unresolved release-version or tag-trigger policy, the
  public package license, required environment reviewers, or pending PyPI Trusted Publisher state;
  those approval and external-configuration gates remain explicit.
- 2026-08-25: Added weekly grouped native-`uv`, npm, container, and GitHub Actions dependency
  updates plus an isolated read-only scheduled/manual mutation workflow. Its target is a fixed
  deterministic observability normalizer, with a 30-minute budget and a strict non-empty gate that
  rejects surviving, uncovered, suspicious, timed-out, interrupted, or crashing mutants. A fresh
  local campaign killed all four selected mutants. Ruff, `ty`, the CI validator, the locked
  dependency check, four maintenance tests, and the complete unit suite pass; the unit suite
  reports 1,018 passed and 93.61% branch coverage. Hosted Dependabot ingestion and scheduled
  execution remain to be verified by GitHub after merge.
- 2026-08-25: PR #59 merged the maintenance foundation as `867ab02`. Exact-head run 32804796522
  and exact-main run 32805306397 passed all 12 jobs, including both rootless E2E profiles,
  document engines, container validation, and the final gate. Dependabot then opened grouped
  Python and GitHub Actions update pull requests, confirming hosted configuration ingestion.
- 2026-08-25: PR #62 merged the independently approved container-evidence foundation as
  `67d27cb`. It binds the same immutable Podman image identity to the retained OCI archive, SBOM,
  vulnerability report, metadata, and an externally anchored manifest. Verification uses bounded
  streaming reads, private atomic staging, exact OCI config/rootfs identity, closed bundle
  membership, and a release-purpose gate for every Critical finding. Exact-head run 32805927412
  and exact-main run 32806442680 passed.
- 2026-08-25: PR #63 merged the independently approved Python-artifact foundation as `eda5793`.
  It builds the wheel and sdist once in private staging; validates bounded classic ZIP/ZIP64, tar,
  metadata, RECORD, and manifest contracts; terminates timed-out descendant process groups; proves
  clean Python 3.14 installation and public import from the exact digest-bound wheel; rejects
  tampering and concurrent replacement; and publishes the verified local bundle atomically without
  replacement. Exact-head runs 32806350066 and 32806985241 and exact-main run 32807062637 passed.
- 2026-08-25: PR #64 merged the independently approved repository-wide workflow validator as
  `255b418`. Current CI and mutation workflows are locked to exact least-privilege contracts. A
  future release workflow must supply an explicit policy with approved trigger/tag patterns,
  distribution identity, artifact path, manifest, and immutable actions; the validator derives and
  binds the real build, integrity verification, clean-install/import, unique upload, and minimal
  PyPI OIDC publish chain. It has no production defaults and adds no release workflow or product
  decision. Exact-head run 32809979039 and exact-main run 32810485581 passed all 12 jobs.
- Remaining implementation is intentionally gated. The project manager must approve the public
  package license; version source and PEP 440/tag mapping; exact release trigger and approved tag
  patterns; scheduled full-suite cadence, timeout, parallelism, and usage budget; and release-image
  registry identity and visibility. GitHub must then have a protected `pypi` environment with
  designated required reviewers, and PyPI must have the matching pending Trusted Publisher. These
  external states were absent when checked. `md-converter` availability must be rechecked immediately
  before the first upload because a pending publisher does not reserve the name.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
