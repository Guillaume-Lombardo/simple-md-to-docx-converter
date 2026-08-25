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

Finalize selective CI/CD, scheduled full suite, mutation testing, dependency updates, release image, SBOM, provenance, and secure publication of the `markweave` Python distribution to PyPI while preserving the public import `md_converter`.

## Scope

- Complete the selective GitHub Actions delivery workflows, scheduled full suite, targeted mutation testing, grouped dependency updates, release image, SBOM, and provenance.
- Add an isolated Python release workflow that builds the sdist and wheel once from the reviewed tagged source, verifies them, and publishes those exact artifacts to PyPI.
- Use PyPI Trusted Publishing with GitHub OIDC, a dedicated GitHub `pypi` environment identity, and a pending Trusted Publisher for the first release instead of a long-lived PyPI token.
- Keep the `pypi` environment free of required reviewers and manual approval; publishing the GitHub
  Release is the sole human release gate, and release-creation permissions are security-critical.
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
- The `markweave` sdist and wheel are built exactly once from the reviewed tagged source and expose
  the documented public import `md_converter`.
- Distribution metadata, installation, the documented public import, and artifact integrity are validated before publication.
- The publication job publishes the exact artifacts that passed validation and never rebuilds them.
- PyPI publication uses Trusted Publishing through GitHub OIDC and the dedicated GitHub `pypi` environment identity; no long-lived PyPI token is created or stored.
- The `pypi` environment has no required reviewer or manual approval, and only a published GitHub
  Release in the trusted upstream repository can start publication.
- Only the minimal PyPI upload job in the isolated workflow receives `id-token: write` for publication. A separate provenance or attestation job owned by T22 may receive `id-token: write` only when the need is documented and all its other permissions are least privilege.
- Every action is pinned by full commit SHA.
- PyPI publish attestations are generated and uploaded with the release artifacts.
- Pull requests, forks, and every other untrusted context are prevented from publishing.
- The release-version and tag-trigger policies are documented and approved before the workflow is implemented.
- Before the first public release, the project manager decides the public package license and configures a PyPI pending Trusted Publisher for the exact GitHub repository, workflow, and `pypi` environment.
- The availability of `markweave` is rechecked immediately before the first publication attempt, which stops if the name is no longer available; a pending Trusted Publisher does not reserve the name.
- The first successful OIDC upload creates the PyPI project and converts the pending publisher into a normal Trusted Publisher, and both the project and publisher state are verified afterward.

## Dependencies

- T03
- T21

## Progress

- Planning scope expanded to include secure PyPI publication in T22; no CI implementation has started.
- Independent planning review clarified the pending Trusted Publisher bootstrap, first-upload
  verification, and least-privilege OIDC boundaries. Its earlier environment-approval proposal was
  superseded by the project manager's no-reviewer policy.
- The official PyPI project and JSON endpoints for `md-converter` returned HTTP 404 on August 23, 2026. This is an availability observation, not a permanent reservation; no PyPI project was created or published.
- 2026-08-25: Started implementation on `feat/T22-release-pipeline` from verified `main` at
  `2c01d4b` after T03 and T21 were confirmed `Done`. Work begins with the implementable CI,
  packaging-validation, dependency-update, release-artifact, SBOM, provenance, and security
  contracts. The workflow will not guess the unresolved release-version or tag-trigger policy, the
  public package license, environment policy, or pending PyPI Trusted Publisher state;
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
- 2026-08-25: Hosted Dependabot ingestion produced grouped Actions and Python update pull requests.
  The raw Actions PR could not satisfy the exact workflow contract, so independently approved PR
  #66 integrated `actions/upload-artifact` 7.0.1 together with its reviewed canonical fingerprint
  and hosted-runner regression tests. The archive behavior stayed enabled, and the Node 24 action
  ran on GitHub-hosted Ubuntu 24.04. Exact-head run 32811871886 and exact-main run 32812499063
  passed all 12 jobs, including retained final-image evidence through the updated action.
- 2026-08-25: Independent review found that the raw Python Dependabot PR omitted Hatchling 1.32's
  new `tomlkit` dependency from the strict build constraints, causing the real hash-required build
  to fail. PR #67 added `tomlkit==0.15.1`, relocked, and canonically regenerated both hashes. Real
  build, integrity verification, clean Python 3.14 installation/import, Metadata 2.5, tamper
  rejection, and rendering/golden tests passed. Exact-head run 32813216652 passed all 12 jobs.
  Exact-main run 32813781840 initially hit a Playwright response-observation timeout after the API
  had already returned `201`; the retained failure evidence confirmed successful account creation.
  Its failed-job rerun passed on the identical `bac665a` source, while the distributed E2E,
  container, storage, engine, and functional jobs passed on the first attempt.
- The former decision gate is resolved. External first-release setup still requires the dedicated
  GitHub `pypi` environment without reviewers and the matching PyPI pending Trusted Publisher; the
  repository workflows do not create either resource.
- 2026-08-25: The project manager approved Apache-2.0, version `0.3` with tag `v0.3`, published
  GitHub Releases as the only release trigger, public image
  `ghcr.io/guillaume-lombardo/md-converter`, and the dedicated `pypi` environment without required
  reviewers or manual approval. PyPI rejected `md-converter`, so the approved public distribution
  is `markweave` while the import remains `md_converter`. The complete suite remains Sunday at
  03:17 UTC with a 45-minute heavy-job timeout and at most two heavy jobs in parallel. The pending
  Trusted Publisher uses project `markweave`, owner `Guillaume-Lombardo`, repository
  `simple-md-to-docx-converter`, workflow `release.yml`, and environment `pypi`.
  Publishing the GitHub Release is the sole human gate; this removes a second-person approval and
  makes GitHub release-creation permissions and the upstream repository guard security-critical.
- 2026-08-25: Implemented the remaining repository release contract. Apache-2.0 and PEP 440 version
  `0.3` are carried by the `markweave` wheel and sdist while `md_converter` remains the public
  import. The published-Release-only Python workflow builds once, verifies integrity, metadata,
  license inclusion, a clean Python 3.14 installation, and the public import, then uploads only the
  verified distributions with OIDC attestations from the `pypi` environment. The independent
  container workflow rootless-smoke-tests the final image, applies the release Critical-vulnerability
  gate, pushes GHCR, attests its digest, and retains and attaches its SBOM and verification evidence.
  The repository validator locks both workflows, immutable action pins, trusted-repository guards,
  exact permissions, and release identity. The scheduled matrix now has `max-parallel: 2` while
  retaining Sunday 03:17 UTC and 45-minute heavy jobs. Both official `markweave` availability
  endpoints returned `404` during implementation. The dedicated GitHub environment, PyPI pending
  publisher, hosted release runs, public GHCR visibility, and post-upload verification remain
  external first-release actions; no release, package, image, or attestation was published here.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
