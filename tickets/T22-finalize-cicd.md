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

Finalize selective CI/CD, scheduled full suite, mutation testing, dependency updates, release image, SBOM, provenance, and secure publication of the `markweave` Python distribution to PyPI with the matching public import `markweave`.

## Scope

- Complete the selective GitHub Actions delivery workflows, scheduled full suite, targeted mutation testing, grouped dependency updates, release image, SBOM, and provenance.
- Add an isolated automatic release workflow that detects a final-version transition on a trusted
  protected-main push, builds the sdist and wheel once from that exact reviewed source, verifies
  them, creates the matching tag and published GitHub Release, and publishes those exact artifacts.
- Permit a manual evidence-only recovery from `main` for an already-created release when its
  version, tag, source SHA, project metadata, published GitHub Release, retained upstream build
  artifact, and public GHCR digest all match exactly; this path must rebuild or republish neither
  the container nor the immutable Python package.
- Use PyPI Trusted Publishing with GitHub OIDC, a dedicated GitHub `pypi` environment identity, and a pending Trusted Publisher for the first release instead of a long-lived PyPI token.
- Keep the `pypi` environment free of required reviewers and manual approval; merging the reviewed
  version-change pull request to protected `main` is the sole human release gate.
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
- The `markweave` sdist and wheel are built exactly once from the reviewed main SHA and expose
  the documented public import `markweave`.
- Distribution metadata, installation, the documented public import, and artifact integrity are validated before publication.
- The publication job publishes the exact artifacts that passed validation and never rebuilds them.
- PyPI publication uses Trusted Publishing through GitHub OIDC and the dedicated GitHub `pypi` environment identity; no long-lived PyPI token is created or stored.
- The `pypi` environment has no required reviewer or manual approval, and only a real final-version
  transition merged to protected `main` in the trusted upstream repository can start publication.
- A `pyproject.toml` change without a `project.version` transition is a successful no-op. Invalid,
  non-canonical, pre-release, development, local, epoch, and downgraded versions fail closed;
  changed canonical spellings with equal PEP 440 precedence remain valid transitions.
- The automation rejects an existing derived tag, matching GitHub Release, or already-published
  PyPI version before creating external state.
- The derived `v<version>` tag and published GitHub Release target the exact reviewed main SHA.
- Tag creation is atomic and precedes Release publication. Failed-job retries accept partial tag or
  Release state only when its complete identity matches the intended tag and reviewed SHA.
- GHCR publication serializes exact registry bytes locally before remote access. Authenticated
  preflight rejects every observed conflicting tag, same-digest state is idempotent, and post-copy
  verification is exact. Workflow/repository concurrency prevents self-races; the documented
  residual inspect/copy race is limited to another principal holding `packages: write` because no
  GHCR conditional manifest creation contract is assumed.
- Future Python artifacts, GHCR tags, attestations, and evidence derive dynamically from
  `project.version`; only tests and documentation may lock the approved first `0.3.0` release.
- Only the minimal PyPI upload job in the isolated workflow receives `id-token: write` for publication. A separate provenance or attestation job owned by T22 may receive `id-token: write` only when the need is documented and all its other permissions are least privilege.
- Every action is pinned by full commit SHA.
- PyPI publish attestations are generated and uploaded with the release artifacts.
- Pull requests, forks, tag pushes, Release events, and every other untrusted context are prevented
  from publishing. Manual dispatch cannot publish Python or container artifacts and may recover
  only provenance and evidence for an exact existing release identity from trusted `main`.
- Manual evidence recovery selects one bounded, non-expired retained artifact by immutable ID only
  after validating the upstream repository, workflow, both release-source-to-run and
  run-to-current-main ancestry, successful source build job, artifact metadata, release identity,
  exact regular-file set, checksum bundle, OCI identity, publication receipt, and anonymously
  readable GHCR digest. It cannot rebuild or write
  Python or container artifacts; only existing attestation and Release-evidence jobs receive the
  validated digest and unchanged evidence.
- The release-version and tag-trigger policies are documented and approved before the workflow is implemented.
- Before the first public release, the project manager decides the public package license and configures a PyPI pending Trusted Publisher for the exact GitHub repository, workflow, and `pypi` environment.
- The exact `markweave` version is rechecked immediately before each publication attempt, which
  stops if it is already published; a pending Trusted Publisher does not reserve the project name.
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
- The former decision gate is resolved. The dedicated GitHub `pypi` environment without reviewers
  and the matching PyPI pending Trusted Publisher are now configured; the repository workflows do
  not create either resource.
- 2026-08-25: The project manager approved Apache-2.0, version `0.3.0` with tag `v0.3.0`, published
  GitHub Releases as the only release trigger, public image
  `ghcr.io/guillaume-lombardo/md-converter`, and the dedicated `pypi` environment without required
  reviewers or manual approval. PyPI rejected `md-converter`, so the approved public distribution
  and public import are both `markweave`. The complete suite remains Sunday at
  03:17 UTC with a 45-minute heavy-job timeout and at most two heavy jobs in parallel. The pending
  Trusted Publisher uses project `markweave`, owner `Guillaume-Lombardo`, repository
  `simple-md-to-docx-converter`, workflow `release.yml`, and environment `pypi`.
  Publishing the GitHub Release is the sole human gate; this removes a second-person approval and
  makes GitHub release-creation permissions and the upstream repository guard security-critical.
- 2026-08-25: Implemented the remaining repository release contract. Apache-2.0 and PEP 440 version
  `0.3.0` are carried by the `markweave` wheel and sdist, which expose `markweave` as the public
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
- 2026-08-25: Before the first publication, the project manager replaced the provisional
  `md_converter` import with `markweave`, aligning the public distribution and import names. The
  source tree, runtime entry points, release validation, coverage and mutation configuration,
  tests, and documentation now use `markweave`; established `MD_CONVERTER` environment variables,
  cookies, metrics, database names, and container product identifiers remain unchanged.
- 2026-08-25: Before the first publication, the project manager refined the first public version
  from `0.3`/`v0.3` to the explicit three-component PEP 440 version `0.3.0` and tag `v0.3.0`.
- 2026-08-25: The project manager recreated and visually confirmed the PyPI pending Trusted
  Publisher for `markweave` with owner `Guillaume-Lombardo`, repository
  `simple-md-to-docx-converter`, workflow `release.yml`, and environment `pypi`. The matching
  GitHub environment exists without required reviewers, deployment restrictions, or secrets.
- 2026-08-25: The project manager replaced manual GitHub Release publication with automatic
  publication after a reviewed version-change pull request merges to protected `main`. The
  workflow now compares the exact push before/head versions, treats unchanged versions as no-ops,
  rejects unsafe versions and pre-existing tag/Release/PyPI state, builds once, creates the derived
  tag and published Release at the reviewed SHA, and publishes Python and dynamically tagged
  container artifacts from the same trusted push. A secretless reusable container workflow
  verifies Release identity before idempotent evidence attachment; token-created tag and Release
  events do not retrigger publication.
- 2026-08-25: Independent review hardened automatic publication against downgrade and remote-state
  races. The detector now compares parsed PEP 440 precedence while preserving an approved
  equal-precedence spelling transition. The creation job atomically writes the exact tag ref before
  the Release and safely verifies partial same-run state on failed-job reruns. Container publication
  binds a source-SHA tag to the locally verified image identity, inspects the remote version tag
  before writing it, accepts only the same digest, and fails closed on observed conflicts.
- 2026-08-25: Re-review reproduced that Podman's OCI-archive manifest digest can differ from the
  registry `dir:` serialization for the same local image. Publication now stages the exact registry
  bytes privately before remote access, validates the staged manifest against Podman's digest file,
  and copies that transport with preserved digests through Skopeo. A publication receipt relates
  the internally verified OCI archive to the registry digest used by provenance. The contract now
  states the accepted limitation precisely: observed conflicts fail before copy and automation
  cannot race itself, while another principal with package-write authority could race the narrow
  preflight/copy interval because GHCR conditional creation is not established.
- 2026-08-25: PR #70 merged the automatic release workflow as `20c1630`. The exact-head CI passed
  all twelve jobs after one unrelated Playwright response-observation timeout passed on a targeted
  rerun. The trusted main push created `v0.3.0` and its published GitHub Release at the exact merge
  SHA and published `markweave==0.3.0` to PyPI through the configured OIDC publisher. The first
  container job exposed a hosted Podman 4.9 compatibility failure before any GHCR push: Podman
  rejects an explicit `--timestamp` when `SOURCE_DATE_EPOCH` is simultaneously exported. The
  follow-up keeps the explicit reproducible timestamp while removing that variable only from the
  Podman process environment, and adds an input-bound manual container-only recovery trigger that
  verifies the existing tag, Release, source SHA, and project version before publication. This
  recovery cannot republish the immutable PyPI version.
- 2026-08-25: Independent recovery review blocked the first hotfix because a manual run's current
  main SHA differs from the historical release SHA, the historical source still contains the
  pre-fix build wrapper, and the complete published Release identity was checked too late. The
  corrected path requires the current SHA only for automatic pushes, always checks out and
  validates the exact release source, verifies the tag and complete non-draft/non-prerelease
  Release tuple before external writes, and invokes historical build scripts with
  `SOURCE_DATE_EPOCH` absent so their own explicit deterministic timestamp remains unambiguous.
  The exact `v0.3.0` historical script and final rootless smoke passed locally through this recovery
  invocation. The normative specification and release operations guide now bound manual dispatch
  to container-only recovery and explicitly exclude Python/PyPI publication.
- 2026-08-25: The corrected recovery published and post-copy verified GHCR digest
  `sha256:4a16b311affb0d0a839350bd145810c1f6044cc7347d12ecd9263fe894de217d`,
  then exposed a second hosted integration gap: the separate provenance job had no registry
  credentials and `actions/attest-build-provenance` failed with `No credentials found for registry
  ghcr.io`. The attestation job now performs its own ephemeral GHCR login immediately before the
  pinned provenance action. Its existing job-local `packages: write`, `id-token: write`, and
  `attestations: write` permissions remain unchanged; no stored secret or PyPI path is added.
- 2026-08-25: Two recovery attempts then exposed a GHCR/Skopeo post-write behavior: Skopeo copied
  every blob and wrote the manifest but exited with status 1 and no diagnostic, so `set -e` stopped
  the job before its existing exact-digest verification. The guarded copy now records Skopeo's
  status without trusting it and accepts a nonzero exit only when an authenticated registry read
  immediately proves that the requested tag resolves to the exact locally staged digest. Missing,
  unreadable, or different remote state still fails closed.
- 2026-08-25: The first hosted execution of that postcondition guard showed that toggling `errexit`
  around Skopeo was not portable to the GitHub runner's `bash -e` invocation: the shell still
  stopped before the recorded status and registry read. Skopeo now runs as the condition of an
  explicit `if`, the Bash-defined context in which a nonzero status does not trigger `errexit`.
  Both branches record the real status before the unchanged authenticated exact-digest postcondition.
- 2026-08-25: The successful `build-and-publish` job in failed recovery run `32846007204` retained
  artifact `9562665677` (`container-release-v0.3.0`) and had already published and verified public
  GHCR digest `sha256:4a16b311affb0d0a839350bd145810c1f6044cc7347d12ecd9263fe894de217d`.
  Later source rebuilds are not byte-reproducible at the registry-manifest boundary, so recovery now
  reuses that exact retained artifact instead of rebuilding. The recovery gate binds the failed run
  to the upstream workflow and requires its SHA to remain between the release source and the current
  trusted `main` workflow SHA, requires its build job and unique artifact,
  validates the downloaded bundle and public digest, then routes only that digest and unchanged
  evidence to the existing provenance and Release-attachment jobs. PyPI and GHCR publication are
  unreachable from this manual path.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
