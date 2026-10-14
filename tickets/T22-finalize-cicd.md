---
ticket: T22
linear_id: G1L-332
linear_url: https://linear.app/g1lom/issue/G1L-332/
status: Done
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
- 2026-08-25: Hosted recovery run `32872650545` on exact `main` SHA `e03681c` successfully reused
  the retained evidence and published the image attestation, but its checkout-free
  `release-evidence` job failed when `gh release upload` implicitly tried to discover a repository
  from `.git`. The upload now passes `--repo "$GITHUB_REPOSITORY"` explicitly, preserving the
  minimal checkout-free job while binding the write to the already validated upstream repository.
  Static policy and mutation tests prevent removal of that explicit repository binding.
- 2026-08-25: PR #75 (`fix(T22): recover retained release evidence`) merged head
  `2ad6bf2fea05ad0f247a71ab761cb492db08d645` as
  `e03681c8c265eb6658251e3fb38d27302dce2d68`. Exact PR CI run `32871203908` passed all 12 jobs on
  attempt 1. PR #76 (`fix(T22): bind Release upload repository`) merged head
  `cb0b1147d1e321849cf1c68315214061772bbfcd` as
  `df39085b7d4124577e6fdcfb80f0b83dcd15b77f`. Exact PR CI run `32873638704` passed all 12 jobs on
  attempt 2. Attempt 1 had unrelated transient browser timing failures in `document-engines`
  (`conversion.browser.test.mjs` test timeout) and `e2e-standalone` (`page.waitForResponse` timeout);
  both passed unchanged on the exact-head rerun, as did the final gate.
- 2026-08-25: Final hosted recovery run `32877372823` succeeded on exact `main` SHA
  `df39085b7d4124577e6fdcfb80f0b83dcd15b77f`: `recover-evidence`, `attest`, and
  `release-evidence` passed, while the publication build was correctly skipped. The published
  `v0.3.0` Release targets `20c16305642ac3349fb333078aea510f8f3d1da1` and contains exactly six
  evidence assets:

    - `image-metadata.json` — `sha256:946e751fecf814c5f2f1478ac07718c83d1352893df12cfea34bcbda21c55360`;
    - `registry-publication.json` — `sha256:439114e03405d763b5eb9661a87bce48b2c7fda0d2f8f329ef555a341e360d5b`;
    - `release-bundle.sha256` — `sha256:5c27d721f838b46e5e6bfa7d2710d89549660ed2c4956374999dca3707d917e8`;
    - `sbom.cdx.json` — `sha256:bf05cd39ed9224c03c1175d71748eda2c2c23563eb8a92ef3c2444a76a1ee3ef`;
    - `sbom.spdx.json` — `sha256:ad8f85ac1af9b439033483977d63f6b72afa2b68258cfca51c05bdcd47c91945`;
    - `vulnerabilities.json` — `sha256:2c0488fd88733348122c200007919ef64106e2cee1c9092dbef07c63e8b2ee4c`.
- 2026-08-25: The public image resolves to
  `ghcr.io/guillaume-lombardo/md-converter@sha256:4a16b311affb0d0a839350bd145810c1f6044cc7347d12ecd9263fe894de217d`.
  `gh attestation verify` succeeded for that subject and digest against repository
  `Guillaume-Lombardo/simple-md-to-docx-converter`. The newest verified identity is
  `.github/workflows/container-release.yml@refs/heads/main`, source/build SHA
  `df39085b7d4124577e6fdcfb80f0b83dcd15b77f`, and invocation
  `actions/runs/32877372823/attempts/1`.
- 2026-08-25: PyPI exposes `markweave==0.3.0` as wheel
  `markweave-0.3.0-py3-none-any.whl`
  (`sha256:863c540cab620e861eebb2841ee3da6b4417a43ba10bd594663a8cd2bd700898`) and sdist
  `markweave-0.3.0.tar.gz`
  (`sha256:ec7b97d9fff9530e7110051604029024014eb425faeced812c815d9c846c4403`). PyPI's integrity API
  verifies publish attestations for both files from the GitHub publisher repository
  `Guillaume-Lombardo/simple-md-to-docx-converter`, workflow `release.yml`, environment `pypi`.
  The successful Python publish job was invocation `32843157962/attempts/1` from exact release SHA
  `20c16305642ac3349fb333078aea510f8f3d1da1`; the pending publisher became the active publisher.
- 2026-08-25: The maintenance foundations are operational: selective and full 12-job CI matrices
  cover both storage and final-image E2E profiles; the Sunday 03:17 UTC full schedule retains its
  45-minute heavy-job timeout and `max-parallel: 2`; grouped Dependabot configuration was ingested
  and opened the reviewed Actions and Python update pull requests; and scheduled mutation run
  `32812214341` passed the strict four-mutant campaign. All actions remain pinned by full SHA.

## Acceptance-criteria verification

- The normative T22 release outcome, English documentation, both storage profiles, rootless image,
  security boundaries, and all dynamic version/tag contracts are implemented and covered by the
  repository-wide static workflow validator. PR #75 and #76 exact-head CI exercised formatting,
  linting, types, unit/functional/integration suites, document engines, container security, both
  final-image E2E profiles, both storage profiles, changed-line and branch coverage, and the final
  gate. No integration or E2E exception is required; the two unrelated browser timeouts passed on
  the unchanged attempt-2 rerun.
- The wheel and sdist were built once from the reviewed main SHA, then their metadata, Apache-2.0
  license, installability, public `markweave` import, closed manifest, and hashes were validated.
  The exact verified files were published without rebuild through OIDC Trusted Publishing; the
  `pypi` environment has no reviewer, secret, or second publication gate. The exact version was
  rechecked before upload, PyPI created the project on first upload, and both file attestations and
  the resulting active publisher identity were verified.
- Automatic publication is restricted to a real final-version transition on trusted protected
  `main`; unchanged versions are no-ops, and invalid, non-canonical, pre/dev/local/epoch,
  lower-precedence, or conflicting remote state fails closed. The tag is created atomically before
  the exact-SHA non-draft/non-prerelease Release, and retry logic accepts only complete matching
  partial state. Tests cover equal-precedence spelling transitions and future versions remain
  dynamic rather than hardcoded.
- GHCR publication stages and verifies exact registry bytes before copy, rejects observed conflicts,
  verifies every post-copy digest, prevents workflow self-races, and uses least-privilege job-local
  package, attestation, OIDC, and Release permissions. Pull requests, forks, tag/Release events, and
  other untrusted contexts cannot publish. Manual dispatch cannot build or publish Python or
  container artifacts; it can only recover an exact retained artifact after repository, workflow,
  ancestry, job, file-set, size, checksum, OCI, receipt, Release, and anonymous public-digest checks.
- The approved Apache-2.0 license, version `0.3.0`, tag `v0.3.0`, public GHCR image, PyPI publisher,
  release trigger, scheduled-suite budget, and parallelism decisions are documented and verified.
  The scheduled-CI contract is statically locked and its identical full matrix passed in hosted PR
  CI; Dependabot ingestion, scheduled mutation testing, SBOMs, vulnerability evidence, Release
  assets, PyPI provenance, and GHCR provenance reached their required hosted boundaries.
- The only residual limitation is the documented narrow preflight/copy race with another principal
  independently holding `packages: write`, because GHCR does not provide an established conditional
  manifest-creation contract. Exact post-copy verification and restricted write authority bound
  this risk; it does not block T22 completion.

## Synchronization

### September 2026 Python dependency maintenance (PR #233)

- Align the bounded WebSocket adapter with Kubernetes SDK 36.0.3, including
  negotiated protocol state, closed-channel tracking, and preloaded response status.
- Keep the attester image, package assertions, and documented SDK pin consistent.
- Update mutation-tool expectations for mutmut 3.8.0 and cover preservation of
  supported HTTP method labels; the targeted campaign kills all six mutants.
- Remove a tautological test assertion diagnosed by ty 0.0.81.
- Local validation: 1,107 targeted broker, integration, package, and tooling tests;
  13 recovery-manifest tests; Ruff and ty; rootless attester image build and isolated
  SDK/entrypoint import smoke test. Hosted CI remains the merge gate.
- 2026-09-19 maintenance: Dependabot PR #231 updates seven `astral-sh/setup-uv` references
  across four workflows to official v10.1.0 commit
  `bec219d24cd3e171d82865faccec33120bb574f4`. Verified that these exact pinned-reference
  substitutions are the entire workflow diff, then refreshed the four canonical policy
  fingerprints. Workflow permissions, triggers, jobs, cache boundaries and commands are
  unchanged. All 184 workflow-policy and CI-runner/integration tests pass; Ruff, ty and the
  local CI security validator pass. This maintains the completed T22 dependency-update
  workflow under the user's explicit CI-first merge and cleanup authorization.

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.

## Patch release 0.6.4 preparation (2026-09-20)

- Requested patch bump from main `0c7dc00`: `0.6.3` to `0.6.4`.
- Synchronize package/application versions, lock metadata, OpenAPI snapshots, release tests,
  source documentation and changelog on `chore/T22-release-0.6.4`.
- Keep published Compose digests unchanged until publication evidence exists.
- Validation passed: `uv sync --all-groups`, Ruff format/check, `ty check`, 219 version/release
  tests, 27 OpenAPI/documentation tests, and the canonical default suite (4,339 passed,
  45 engine tests deselected; 94.87% total coverage). Version/changelog transition and
  `git diff --check` also pass.
- The unrestricted engine suite and final-image E2E were not run for this metadata-only bump;
  Pandoc, Mermaid CLI, and LibreOffice executables are unavailable on PATH. Frontend code is
  unchanged, so JavaScript checks were not rerun. Protected hosted CI remains the publication gate.
- The user authorized publication, protected squash merge and exact source-branch cleanup
  through the `yolo` skill on 2026-09-20. Hosted checks and independent review remain required.

2026-09-20: User requested splitting the light job after all 4,024 Python tests
and all thresholds passed but the runner reached its 20-minute job limit.
Rapid/frontend checks are separated from two deterministic complementary Python
partitions. Same-attempt artifact aggregation preserves total, branch and changed-line
90% gates; every partition and aggregate result is mandatory in CI / gate.
The selector and workflow policy have 193 passing focused checks, including real
pytest marker selection and invalid-argument rejection. Full partition execution
and independent implementation review are in progress; no release gate is bypassed.

Independent review found and resolved two issues before publication: shard commands
now disable only the local per-run branch coverage hook, and both new uv jobs restore
the existing lock-keyed cache without saving from PRs. The user additionally requested
caching; light CI now restores the Next.js compiler cache, keyed to Node/lockfile,
and saves it only on trusted main. No test/coverage outcomes are cached. Independent
re-review found no remaining blocker. Full local partition runs are pending; Ruff,
ty and the exact workflow policy pass. Previous hosted heavy domains all passed;
the old unsplit light retry is superseded by the requested CI improvement.

The full collection audit caught generated parameter IDs containing timestamps and
UUIDs. The partition key now excludes parameter IDs and keeps each function's cases
together. Three real collections prove exact per-function case-count union: 4,042
selected cases, split 2,129/1,913, with no shared function and no missing case.
The independent reviewer confirmed this fix; all 193 focused tests still pass.
An earlier local split completed in 5m06s/5m34s with aggregate 90.10% branches,
but its random-parameter allocation is not accepted as final coverage evidence.
The corrected full partitions are running again. Hosted rapid light passed in 1m33s;
pnpm cache restoration is verified, new uv/Next.js cache entries await trusted main.

2026-09-20: PR #244 is merged as d0c150f625cce9e51102904ab9289721d6dd7054.
All required checks passed, including the final split Python suite and aggregate
coverage. Stable local partitions passed 2,129/1,913 cases in 5m14s/5m20s; hosted
partitions passed in 8m13s/7m29s and aggregate coverage in 14s. Main populated the
uv and Next.js caches successfully. Independent review has no unresolved findings.
Release run 35519480791 is successful: PyPI 0.7.0, both exact GHCR images, SBOMs,
provenance and matching source/lockfile-bound receipts are published. The adoption
follow-up pins these published digests in Compose, both quickstarts and their E2E
fixture. T81/T82 are verified on main and complete; T83 stays Backlog. T22 remains
In Progress until the adoption change is verified on main and final records close.

## 0.7.0 release closure (2026-09-20)

- Feature and CI PR #244 is verified on main at
  `d0c150f625cce9e51102904ab9289721d6dd7054`; main CI `35519480601` succeeded.
- Automatic release `35519480791` completed successfully. PyPI 0.7.0 and both
  GHCR images, SBOMs, provenance and receipt evidence are published.
- Adoption PR #245 passed every required check in run `35521398515`, including
  Compose, container and both final-image E2E profiles. Independent exact-head
  review has no unresolved finding. It merged as
  `cd6bb29c87c87db9f1f9279f7db41a5728767670`, verified on main.
- Compose, both quickstarts and the E2E fixture now use the exact published 0.7.0
  backend/frontend receipt digests. Anonymous public release alignment is verified.
- GitHub Actions restored uv, pnpm and Next.js caches on the adoption PR. Light
  passed in 1m22s, Python partitions in 8m37s/8m46s and aggregate coverage in 15s.
  All 90% thresholds remain mandatory; no tests or coverage results are cached.
- T22, T81 and T82 are complete. Advanced reverse presentation extraction remains
  the explicitly separate T83 backlog scope.
- The feature/adoption source branches are removed after verified squash merges.
  The unrelated worktree with uncommitted 0.6.4 adoption work is preserved.
- Docker-box remains on the previous test candidate: SSH authentication is
  unavailable in this session. No deployment of the published 0.7.0 pair is claimed.


## October 12, 2026 Dependabot maintenance

- Review Python PR #294 and Actions PR #280 against main at e37e7b0.
- Retain Kubernetes 36.0.3: SDK 37.0.0 changes the generated exec method contract
  and fails static compatibility checks; its migration requires separate qualification.
- Update PDFium 5.14.0 qualification while preserving historical golden provenance.
- Refresh the exact workflow fingerprints and action-pin regression assertions.
- Preserve project.version 0.7.5 and its published receipt digests.
- Mermaid PR #274 is incompatible with the current adapter: CLI 12 removes
  --width/--height, which src/markweave/conversion/mermaid.py still passes.
  Keep CLI 11.17.0 pending an adapter migration and engine/golden/final-image
  qualification; do not refresh checksums merely to make the raw bump pass.
- Canonical build constraints are regenerated from uv.lock, retaining the
  explicitly constrained trove-classifiers version rather than Dependabot
  export drift.
- Targeted checks: 219 workflow/package/PDFium tests and 73 Kubernetes/package
  integration tests pass; hash-required sdist/wheel build passes; Ruff, ty and
  the CI security validator pass. ty 0.0.85 rejects 24 compatibility warnings;
  update context-manager Generator/AsyncGenerator annotations and remove one
  redundant cast, then require a clean ty result. These changes preserve runtime
  behavior; the existing boundary tests cover the same paths.
- Canonical default suite: 5,880 passed, 114 engine/runtime cases deselected,
  15 warnings; 94.36% total coverage and 90.48% branch coverage.
- Changed application coverage passes at 100.00% (22/22 lines).
- Pytest reports 15 non-blocking warnings from Starlette/httpx, pypdf,
  SQLAlchemy, and legacy session configuration.
- Separate branch checks: 201 Actions tests and 85 Python/package/PDFium checks
  pass; Ruff formatting/lint and ty pass cleanly on each branch.
- All nine application modules touched for ty compatibility retain their
  executable bodies and line positions. All four workflow diffs are limited
  to the independently verified immutable Action substitutions.
- Full document-engine and final-image
  qualification remains a hosted CI gate; Pandoc, Mermaid and LibreOffice
  executables are unavailable on the local PATH.
- Publication and merge required explicit AGENTS.md approval; the user granted it
  before the authorized updates below.

## Prepared pull-request updates

### PR #294: `chore/T22-python-dependencies`

Changed files relative to current main:

- `build-constraints.txt`
- `docs/pdf-conversion.md`
- `pyproject.toml`
- `scripts/generate_t11_pdf_golden.py`
- `scripts/release/artifacts.py`
- `scripts/release/process.py`
- `scripts/testing/services.py`
- `src/markweave/broker/inventory_storage.py`
- `src/markweave/broker/kubernetes_attester_inventory.py`
- `src/markweave/conversion/libreoffice.py`
- `src/markweave/http/lifecycle.py`
- `src/markweave/jobs/worker_execution.py`
- `src/markweave/observability.py`
- `src/markweave/persistence/migrations/versions/20260920_18_presentations.py`
- `src/markweave/persistence/sql.py`
- `src/markweave/recovery_adapters.py`
- `tests/integration/auth/test_http_session.py`
- `tests/integration/broker/test_broker_process_integration.py`
- `tests/integration/document_engines/test_pdf_rasterizer.py`
- `tests/integration/test_cli_composer_http.py`
- `tests/unit/broker/test_kubernetes_deployment.py`
- `tests/unit/test_cli_local_outputs.py`
- `tickets/T22-finalize-cicd.md`
- `uv.lock`

### PR #280: `chore/T22-workflow-actions`

Changed files relative to current main:

- `.github/workflows/ci.yml`
- `.github/workflows/container-release.yml`
- `.github/workflows/mutation.yml`
- `.github/workflows/release.yml`
- `scripts/ci/validate_ci.py`
- `tests/test_ci_validation.py`
- `tickets/T22-finalize-cicd.md`

The existing Dependabot head and current main are ancestors of each local branch; publication uses a normal fast-forward push to the existing PR branch, without history rewriting. This prepared snapshot preceded the authorized remote updates recorded below.


## Authorized integration progress

- The user explicitly approved normal pushes to PRs #280 and #294, independent
  review, squash merges after successful CI, and cleanup of task branches.
- Independent review found no actionable issues. Additional review checks passed:
  201 workflow tests, eight real PDFium tests, 50 Composer connection/encryption
  tests, static checking, canonical constraints and immutable upstream Action pins.
- Initial hosted CI passed all 19 jobs on both reviewed heads: run 38245391028
  for #280 and run 38245415833 for #294. These runs include document engines,
  golden checks, storage, coverage, and both final-image E2E modes.
- PR #280 was independently squash merged on October 12, 2026 at 12:36:30 UTC
  as 745367ff3fb4cadadd6f140a2954b4e6f18567ee, without bypassing protections.
- PR #294 incorporates that main commit through a normal merge. The refreshed
  base passes uv sync, Ruff formatting/lint, ty, the CI validator, and 201
  workflow tests. Its new head must pass hosted CI and independent review
  before the separately authorized squash merge.
- T22 remains In Progress while Mermaid #274 awaits its adapter migration
  and engine/golden/final-image qualification. The published version remains
  0.7.5 and Compose receipt digests remain unchanged.


## Remaining Actions update (#299)

- Dependabot opened #299 during remaining-PR qualification. Prepare it on
  `chore/T22-setup-uv-10.3`, based on main f31640b and the existing bot head.
- Update all 11 setup-uv uses in four workflows to the official v10.3.0 commit
  1c37ad07a6a961277cf70c0d37d6f313000f5884; retain the pinned uv 0.12.1,
  Node 24 runtime, inputs, cache settings, triggers and permissions.
- Upstream architecture selection returns immediately when the new input is
  empty, as in this repository; its uv 0.13 requirement is not activated.
- Refresh CI/mutation semantic hashes and release/container exact-text hashes.
  The unchanged pair-publisher script keeps its existing hash. Initial hash
  assignment used the wrong policy/hash mode, which validation rejected;
  correct assignment passes the repository module validator.
- All 201 policy tests pass. The initial focused invocation also applied global
  application coverage, exiting one despite those passes (3.92% total);
  a focused rerun disables coverage collection only. Whole-application coverage
  remains mandatory in hosted CI. No application source changes in this PR.
- uv sync, Ruff formatting/lint, ty, CI policy and diff whitespace checks pass.
  Independent upstream/source review has no outstanding finding.
- Hosted exact-head execution remains required before squash merge. No remote
  update, publication or merge has occurred; explicit user approval is pending.
- T22 stays In Progress; project.version and public receipts are unchanged.

### Prepared publication: `chore/T22-setup-uv-10.3`

Changed files relative to main:

- `.github/workflows/ci.yml`
- `.github/workflows/container-release.yml`
- `.github/workflows/mutation.yml`
- `.github/workflows/release.yml`
- `scripts/ci/validate_ci.py`
- `tickets/T22-finalize-cicd.md`


## October 14, 2026 authorized refresh (PR #299)

- User explicitly requested rebasing onto main and finishing remaining Dependabot
  PRs. Unpublished local commits are rebased onto main 7a28b70; the existing
  remote bot head is then incorporated for normal fast-forward publication.
- Preserve T130/T135/T139 CI domains, image-build jobs, E2E partitions, caches
  and coverage gates; conflicts refresh policy hashes without replacing new logic.
- Fresh uv sync, Ruff formatting/lint, ty and module CI validator pass.
- Local validation: 233 workflow checks; unchanged application source. All 13 Action uses retain existing inputs and cache settings.
- Rebase validation encountered stale timestamp-based Python bytecode after
  Git restored files; a separate bytecode cache verifies the actual current
  source in fresh processes. No CI policy failure is suppressed.
- Prior October 12 full-suite/native/image results are historical evidence,
  not a claim of full execution on this updated main. Fresh hosted exact-head
  CI and independent review are required before the authorized squash merge.
- T22 remains In Progress; version 0.7.5 and published receipts are unchanged.


## Mermaid 12 qualification (#274)

- Replace removed width/height options with a fixed size bounded by the smaller
  configured axis; retain aspect-ratio normalization and physical document caps.
- Fix the default theme and Liberation Sans family to avoid Mermaid 12 choosing
  diagram-specific themes/fonts outside the reviewed system-font contract.
- Synchronize runtime version reporting, future golden generation and smoke checks;
  preserve historical golden artifacts and provenance.
- Native engine, golden and final-image qualification remain required.
- No project version or published image-pin change is included.

### Mermaid engine and security qualification

- Keep CLI 12.0.0, resolving its compatible Mermaid dependency to the official
  integrity-bound 12.1.0 build, which embeds Lodash ES 4.18.1. No local vendor
  patch or misleading standalone dependency override is used.
- Refresh the CI lock checksum, its assertions and canonical workflow fingerprint
  only after frozen installation, source review and native engine qualification.
- npm audit has no High/Critical alert but reports the Low KaTeX advisory
  GHSA-238p-pmpm-9mq7 through four dependency nodes. Mermaid 12.1.0 still embeds
  KaTeX 0.16.47; a standalone override does not replace that compiled copy.
  This is a documented mitigation, not a claim that bundled KaTeX is patched.
- The advisory requires existing prototype pollution or attacker-controlled
  options and unsanitized HTML insertion. The adapter rejects configuration
  directives/frontmatter, fixes options, starts a fresh sandboxed browser with
  no runtime network, and emits normalized bounded PNG only. Upstream Mermaid
  sanitizes the KaTeX HTML before rendering. Native math cases include untrusted
  hyperlink and external-image expressions; no HTML output reaches the application.
- Upstream reference: https://github.com/KaTeX/KaTeX/security/advisories/GHSA-238p-pmpm-9mq7
- The initial 48 native tests and 642 conversion/workflow tests pass; the final
  graph is requalified after its security and checksum corrections. Canonical
  Python suite and hosted final-image E2E remain pending.


## Final local qualification for remaining Dependabot PRs

- PR #294 is verified merged as f31640beb323a7c59474af608aa9ce25b9989352;
  its main CI run 38255468046 passed all 19 jobs.
- The frozen combined candidate a414ba19a2f2975b26863bc1d0ffc54644e91c0e
  passes the canonical default command: 5,886 passed, 118 engine/runtime cases
  deselected, 15 warnings, 4,562.93 seconds, exit zero.
- Coverage is 94.36% total and 90.47% branches (8,591 of 9,496).
  The changed-executable-line gate passes with zero executable changed lines;
  protocol annotations are excluded and constant/argument changes are traced
  through their unchanged enclosing statements, which executed in the suite.
  This is not a claim of positive-line coverage from a zero-line denominator.
- An earlier run was interrupted after 1,622 passes and five policy failures
  because its imported workflow fingerprint preceded changes during execution.
  That inconsistent run is not accepted; the fresh frozen run above supersedes it.
- The latest Mermaid bot head 1ab86db07ccf89a307a1bd02196df4e78a572920
  is integrated by normal merge. Its seven transitive package refreshes leave
  application source identical to the full-suite candidate. Official registry
  identities and licenses were independently reviewed. The final lock checksum
  is 7c482e9ea57e31a772ac7004d24cab0cad11bbf1dd5911f3d4019a3202c1e3e2.
- The refreshed graph passes 279 focused checks, 51 native document-engine
  checks, and npm audit signatures (194 signatures, 54 attestations verified).
  npm audit still exits one for four Low occurrences of the documented KaTeX
  advisory; uv audit exits one for the same six baseline entries as main.
- Final Mermaid image 888a9441cbb173acea1495e4874e7c67ed129d664d6559f10d45a210c78c9c25
  and Python image 155d892c5504636f386466cfe1dd3bcfc0e606a530501d2f540ad8cfee8fc8ab
  pass rootless smoke, local conversion E2E, standalone API conversion, and
  distributed conversion/SIGTERM/lease-recovery E2E. The SDK 37 attester image
  also builds and passes non-root import without activating Kubernetes.
- An earlier Mermaid distributed harness execution exited 141 (SIGPIPE);
  its unchanged image passed a diagnostic rerun. The refreshed final Mermaid
  image passes all scripts on its first run, exit zero.
- Ruff formatting/lint, ty, CI policy and hash-required packaging checks pass.
  Direct-file CI validator invocation failed to resolve its scripts package;
  the repository module invocation `uv run python -m scripts.ci.validate_ci`
  succeeds. This invocation error required no source change.
  The unrestricted host pytest suite is unavailable with Pandoc, Mermaid and
  LibreOffice absent on PATH; actual native/container evidence is recorded above.
  Hosted exact-head CI remains mandatory before merging; no E2E exception applies.
- Independent review has no outstanding finding. Remote publication, squash
  merges and cleanup await the explicit repository-required user approval.
  T22 stays In Progress. Version 0.7.5 and published receipt digests are unchanged.

### Prepared publication: `chore/T22-mermaid-12`

Changed files relative to main:

- `.github/workflows/ci.yml`
- `docs/conversion-cli.md`
- `docs/mermaid.md`
- `docs/t00-toolchain-validation.md`
- `scripts/ci/validate_ci.py`
- `scripts/container/smoke.sh`
- `scripts/generate_t11_pdf_golden.py`
- `src/markweave/conversion/mermaid.py`
- `src/markweave/conversion/runtime_manifest.py`
- `tests/integration/document_engines/test_mermaid_cli.py`
- `tests/test_ci_validation.py`
- `tests/test_pnpm_workspace.py`
- `tests/unit/conversion/test_mermaid.py`
- `tickets/T22-finalize-cicd.md`
- `toolchain/document-engines/THIRD_PARTY_NOTICES.md`
- `toolchain/document-engines/package-lock.json`
- `toolchain/document-engines/package.json`


## October 14, 2026 authorized Mermaid refresh

- User requests rebasing onto main and finishing remaining Dependabot PRs.
  Unpublished commits rebased onto 7a28b70; remote bot ancestry retained for
  normal publication, preserving current CI partitions, caches and gates.
- uv sync, Ruff formatting/lint, ty, module CI validator and 299 focused
  workflow/workspace/renderer checks pass. Final npm graph SHA7c482 remains
  identical to the earlier native/image qualification; historical October12
  results do not substitute for fresh hosted exact-head CI.
- Rebase conflicts preserve current validator logic and recompute its fingerprint.
  A fresh bytecode cache avoids stale imports after Git timestamp restoration.
- T22 remains In Progress; version0.7.5 and published receipts unchanged.


## October 14 integration after Actions merge

- #299 passes all23 hosted jobs in38486940940 and independent review, then
  protected squash merges as9eef41ed1b474aa92e74b52641d8781efd713729.
- Initial #274 CI38486946802 and #305 CI38487115966 also pass all23 jobs.
- Frozen combined b681405 passes6309 default tests,131 deselected,15warnings
  in427s;94.44%total and90.55%branch coverage. Changed-executable-line gate
  has zero executable changed lines, not a positive-line coverage claim.
- The earlier788-pass localrun was interrupted when temporary checkouts were
  externally removed. Fresh unchanged source in the preserved checkout passes.
- Merge current main normally; preserve workflow policies, font notices and
  immutable npm graph. Refresh the one combined CI fingerprint.
- Require fresh hosted CI and independent exact-head review before merging #274.
  Project version and public receipts remain unchanged; T22 stays In Progress.


## Remaining Python Dependabot qualification (#298)

- Qualify Kubernetes 37.0.0 against the existing bounded exec adapter and SDK
  integration tests; narrow the protocol to the exact keyword arguments used.
- Adopt pypdf 6.20.0 and regenerate canonical hash-required build constraints.
- Preserve the paused Kubernetes capability, version 0.7.5 and public image pins.
- Validation and remote publication are pending.


## October 14, 2026 Python qualification (#305)

- #298 closed without merge and is superseded by #305. Retain its reviewed
  bounded Kubernetes protocol and four real generated-SDK integration tests,
  updating all package, attester and assertion pins to Kubernetes37.0.1.
- Adopt pypdf6.20.0, boto3 1.43.111, Ruff0.17.0 and ty0.0.86. Canonical
  constraints export retains the explicitly constrained trove-classifiers
  2026.6.1.19; no unrelated build-constraint drift is adopted.
- Current main7a28b70 and bot head fe3ecbe are ancestors; normal push is valid.
- ty0.0.86 rejects conditional scanner dictionary expansion in one test helper
  with 28 argument errors. Use an explicit scanner argument with the same
  TrustingUploadScanner default; no application executable body changes.
- uv sync, Ruff format/check, ty and module CI validator pass; 1,156 targeted
  broker/package/HTTP-adapter/release checks pass (one dependency warning).
  Hash-required sdist/wheel build and integrity validation pass.
- uv audit exits one with exactly the same six advisory/package/version entries
  as current main, no introduced alert. No baseline finding is suppressed.
- A frozen combined canonical suite and fresh hosted exact-head CI will verify
  current-main integration; October12 native/image/default-suite evidence is
  historical, not a current-main full-run claim. Host engine binaries remain
  unavailable; hosted native and final-image E2E are mandatory.
- User authorizes finishing remaining PRs, including protected squash integration
  after checks and independent review. T22 stays In Progress until verified main.
  Kubernetes stays paused; project.version and public receipts are unchanged.


## Python refresh after protected Actions integration

- #299 verified merged as9eef41ed1b474aa92e74b52641d8781efd713729 after
  all23 hosted jobs and independent review. Incorporate that main normally.
- Initial #305 CI38487115966 passes all23 jobs; reviewed source unchanged.
- Current-main frozen combined b681405 canonical6309tests pass,131deselected,
  15warnings,427s exit0;94.44%total and90.55%branch coverage. Changed-line
  gate has zero executable changes, not positive-line coverage evidence.
- Interrupted788-pass run after external checkout removal is superseded by
  fresh unchanged source success in the preserved original workspace.
- Require fresh hosted CI and independent review on this refreshed head before
  protected squash. T22 stays In Progress; release version/receipts unchanged.


## Final Python integration candidate after Mermaid

- #274 protected squash df55048c0fc195b70e1ea19840d27f47ea08f419 is verified
  merged after fresh23-job CI38489206877 and independent exact-head review.
- #299 squash9eef41e is also verified merged. Both remote sources are absent.
- #305 refreshed pre-Mermaid CI38489315177 passes all23 jobs; normally merge
  the latest main and require fresh exact-head CI and independent review.
- Application/source test and dependency graph trees match the frozen b681405
  combined candidate that passed6309 tests,131deselected,15warnings,
  94.44%total and90.55%branches. No repeated full local run is needed for
  this documentation/history-only refresh; hostengine tests remain unavailable.
- Baseline Python audit six entries and mitigated KaTeXLow remain documented.
  T22 stays In Progress until this final dependency patch is verified on main.
  No version/publicimage receipt change or Kubernetes backend activation.


## October 14, 2026 maintenance closure

- All remaining Dependabot changes are verified on main:
  - #299, setup-uv 10.3: squash `9eef41ed1b474aa92e74b52641d8781efd713729`.
  - #274, Mermaid CLI 12: squash `df55048c0fc195b70e1ea19840d27f47ea08f419`.
  - #305, Kubernetes 37.0.1 and Python dependencies: squash
    `bec1fe8ea7b3b756b1474ba01d19f2ac8bc4a333`.
- Each final reviewed head passes all 23 hosted jobs in runs 38486940940,
  38489206877 and 38491319506 respectively. These include actual document
  engines, four final-image E2E partitions, both storage profiles, containers,
  supply-chain verification, mutation checks and all coverage gates.
- Independent exact-head review precedes every protected squash merge. No
  force-push or protection bypass was used; remote source branches are absent.
- Merged application, test and dependency trees match frozen candidate b681405.
  Its canonical default suite passes 6,309 tests with 131 engine/runtime cases
  deselected and 15 warnings in 427 seconds. Coverage is 94.44% total and 90.55%
  branches (8,628/9,528). Changed executable coverage has zero changed executable
  lines; this does not establish positive-line coverage from a zero denominator.
- Earlier interrupted local runs and the old-image SIGPIPE failure remain
  historical failure evidence. The fresh frozen local suite and all final
  hosted-head runs pass. Incorrect validator/module invocations were corrected;
  no failing policy, audit or coverage result is presented as a pass.
- The unrestricted host suite remains unavailable without Pandoc, Mermaid and
  LibreOffice on PATH. Hosted native and final-image execution provides their
  required qualification; no integration or E2E exception was requested.
- Python audit retains exactly six main-baseline entries and no introduced
  finding. The final npm audit reports three Low nodes for the documented,
  mitigated KaTeX advisory, with no Moderate/High/Critical finding. Bundled
  KaTeX remains unpatched; both audit commands exit one, not a clean-audit result.
- Main CI after the Actions and Mermaid merges passes all 23 jobs in runs
  38489099736 and 38491199400. Final Python main CI is monitored separately.
  Its first light-job attempt rejects an invalid public GitHub release response;
  the identical public-alignment command passes locally on unchanged source.
  GitHub refuses a targeted rerun while that workflow is active; its failed
  jobs will be rerun after completion, with terminal evidence retained in Linear.
- Linear G1L-332 is Done only after the three dependency changes are verified
  on main. This documentation change mirrors that state and durable evidence.
- Version 0.7.5, public image receipt pins and the paused Kubernetes capability
  remain unchanged. Cleanup is limited to this task's branches and four local
  qualification images; unrelated worktrees are preserved.
