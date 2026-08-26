---
ticket: T23
linear_id: G1L-333
linear_url: https://linear.app/g1lom/issue/G1L-333/
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T23 - Complete user, developer, and operations documentation

## Objective

Complete English user, template, administrator, API, operations, storage, queue, agent, recovery, and deployment documentation.

## Acceptance criteria

- The implementation satisfies the T23 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.
- The README leads a casual user from prerequisites through a healthy local service and first
  conversion before presenting operations, architecture, and development details.
- A tested root `compose.yaml` runs the released standalone image with real ClamAV, persistent data,
  loopback-only HTTP, immutable image identities, and the final rootless security contract.
- The quickstart offers both the physically bounded, sudo-assisted ext4 workspace and a clearly
  labeled sudo-free alternative backed by an ordinary, physically unbounded Docker or rootless
  Podman volume.
- The documentation index exposes role-based user, API, configuration, operations, recovery,
  deployment, architecture, development, and agent guides; every runtime setting and local link is
  checked automatically.
- TLS-terminating deployments can configure one exact public HTTP(S) origin without trusting
  forwarded headers, while direct HTTP behavior remains backward compatible when it is unset.
- After both quickstarts are verified on `main`, version `0.3.1` is published through the existing
  automatic release path and Compose is repinned to the immutable `0.3.1` image digest.

## Dependencies

- T22

## Progress

- 2026-08-25: Started after T22 was verified `Done`. The documentation will lead with a
  casual-user quick start backed by a tested `compose.yaml`, then provide progressively deeper
  user, operations, architecture, development, recovery, and agent-workflow guidance.
- 2026-08-25: Implemented the role-based documentation set, complete runtime configuration
  reference, current architecture and operations guidance, safe release/recovery procedure, and a
  casual-user README. Added `MD_CONVERTER_PUBLIC_ORIGIN` with unit and real-HTTP integration tests
  so the documented TLS reverse-proxy design keeps exact Origin validation without trusting
  forwarded headers.
- 2026-08-25: The Compose evaluation profile was exercised with the published `0.3.0` image and
  official ClamAV 1.4 LTS image at pinned digests. Both services became healthy,
  `/health/ready` returned HTTP 200, only `127.0.0.1:8080` was published, ClamAV had no host port,
  the application had no Internet egress, and persistent data/signature volumes survived the
  non-destructive stop. Six documentation/Compose contract tests and 138 targeted tests pass;
  Ruff formatting and linting, `ty`, `docker compose config`, and `git diff --check` pass.
- 2026-08-25: The canonical suite excluding Pandoc, Mermaid, and LibreOffice passes with 1,514
  tests and 95.54% coverage when the documented PostgreSQL/S3 test services are supplied. The full
  host suite reaches 1,521 passing tests and 95.49% coverage but cannot run 37 engine tests because
  Pandoc, Mermaid/Chromium, LibreOffice, and the locked fonts are not installed on the host; the
  pull-request matrix must verify those final-image engine boundaries before completion.
- 2026-08-25: Independent review corrected the quickstart template instructions, the proxy-header
  trust boundary, runtime-variable inventory, local-development guidance, golden-test wording, and
  Markdown link/anchor validation. The Compose profile now uses a disk-backed bounded work volume
  with memory headroom, and normal shutdown removes only that disposable volume after validating
  its exact Compose labels while preserving application data and ClamAV signatures.
- 2026-08-25: Added an active real-Compose CI domain. Its isolated final-image workflow uploads the
  committed template, performs a combined DOCX/PDF conversion, verifies audit and result access,
  checks loopback exposure, ClamAV reachability and blocked application egress, removes and
  recreates only the work volume, restarts the stack, and verifies durable recovery from `/data`.
  The final-image runner also proves that the configured public origin accepts a valid login and
  rejects a hostile Origin even when `Forwarded` and `X-Forwarded-*` headers are spoofed.
- 2026-08-25: Exact-SHA review rejected an unbounded Docker work volume and proposed E2E exceptions.
  The Linux/rootful-Docker quickstart now creates a private, exact 256 MiB ext4 loop filesystem
  through one user-facing script, proves physical `ENOSPC`, and safely handles password reuse,
  obsolete volumes, restart, rollback, and shutdown. The Compose E2E drives that same script,
  reformats disposable scratch after an abnormal stop, proves recovery from durable `/data` state,
  verifies failed-start rollback under a real port collision, and proves that stale loop-device
  metadata neither detaches nor changes the bytes of an unrelated reused device. The helper accepts
  only an AMD64 Linux host using the standard local rootful Docker Unix socket.
- 2026-08-25: Eliminated the rejected E2E exceptions. Both standalone and distributed final-image
  suites now cover a ZIP/SVG success path with normalized PNG evidence; corrupt ZIP, encrypted ZIP,
  and invalid-image failures with no result; and exact macro, external-relationship, missing-style,
  and unsupported-font template rejections with no catalog publication. Both complete final-image
  suites pass.
- 2026-08-25: Pull request #78 passed every required hosted domain, including the privileged
  Compose lifecycle, both final-image E2E profiles, storage, document-engine, functional, container,
  infrastructure, and final gate jobs. It was squash-merged to `main` as commit `9309880`, completing
  and verifying all T23 acceptance criteria.
- 2026-08-25: Reopened for a requested usability follow-up. The existing physically bounded ext4
  quickstart remains available as the secure sudo-assisted variant; a second tested path will run
  without sudo on Docker or rootless Podman by accepting an ordinary engine-managed volume for
  `/work` and documenting its weaker physical-capacity isolation.
- 2026-08-25: The PM requested a `0.3.1` release after the two quickstarts are verified. The
  existing automatic version-release workflow will publish the tag, GitHub Release, PyPI package,
  and GHCR image; Compose will then be repinned to the published immutable image digest.
- 2026-08-25: Implemented the sudo-free quickstart with an ordinary unbounded `/work` volume for
  Docker Compose and rootless Podman Compose while retaining the existing 256 MiB ext4 secure
  path. The helper validates exact volume ownership before cleanup, serializes commands over its
  private state, waits for real ClamAV and application readiness, preserves durable volumes across
  restart and rollback, and removes its private Podman API service after each command. The real
  Docker and Podman lifecycle suites pass; Podman also completed a Mermaid DOCX/PDF conversion.
  Ruff, `ty`, ShellCheck, the CI validator, 241 focused tests, and `git diff --check` pass.
- 2026-08-25: PR #80's first hosted Compose run exposed a GitHub Ubuntu 24.04 runner mismatch:
  recent Podman generated OCI 1.2.1 configuration but selected the older `/usr/bin/crun` 1.14.1
  instead of the newer executable already first in `PATH`. The private Podman configuration now
  selects that current `crun` when available and otherwise uses Podman's declared runtime path,
  without changing global host configuration.
- 2026-08-25: PR #80 passed every required hosted domain, including both sudo-free Docker and
  rootless Podman Compose lifecycles, and was squash-merged to `main` as commit `bc43e885`. Prepared
  the requested `0.3.1` version transition; publication and the final immutable Compose repin remain
  pending.
- 2026-08-26: PR #81 passed its complete hosted matrix and was squash-merged to `main` as commit
  `8133385d`, but GitHub rejected the automatic release before starting any job. The reusable
  container workflow's recovery-only `artifact-run-id` input was not declared for `workflow_call`;
  declaring it as an optional automatic-call input passed a real reusable-call canary while every
  publication and recovery job remained skipped. No `v0.3.1` tag, GitHub Release, PyPI
  distribution, or GHCR image was created by the failed run or canary.
- 2026-08-26: The PM authorized release recovery. Because rerunning the failed push would preserve
  its invalid workflow SHA and manual publication is forbidden, recovery uses two protected
  version-transition pull requests. This first change restores every live version surface to the
  already published `0.3.0`; the automatic detector must fail closed on that downgrade without
  creating external state. A second reviewed merge will reapply `0.3.1` from the corrected
  workflow and start the real automatic publication.
- 2026-08-26: Recovery PR #83 exposed an existing browser-test race in the distributed final-image
  workflow. Account creation returned `201`, but the helper attempted the next submit after the
  success message and before the form's asynchronous user-list refresh released its submission
  guard, so the second submit was correctly ignored and the response wait expired. The helper now
  waits for that observable guard to clear before creating the next account.
- 2026-08-26: Recovery PR #83 passed its complete hosted matrix and was squash-merged as `4b11b46d`.
  Its downgrade created no release state, but the automatic workflow exposed the next reusable-call
  validation failure: the caller did not grant the recovery path's required `actions: read`
  permission. The automatic container call now grants that read-only permission explicitly, and
  the exact release policy rejects its removal. Reapplying `0.3.1` remains blocked until this
  startup correction passes protected review and a trusted-main canary.
- 2026-08-26: PR #84 passed its complete hosted matrix and was squash-merged as `9220cd54`. A
  protected metadata-only `pyproject.toml` change now triggers the corrected automatic workflow
  while retaining version `0.3.0`; the detector must report no version transition and every
  publication job must remain skipped before `0.3.1` is reapplied.
- 2026-08-26: PR #85 passed its complete hosted matrix and was squash-merged as `1abdda78`. Its
  trusted-main canary run completed successfully: version detection passed, every build and
  publication job was skipped, and the `v0.3.1` tag, GitHub Release, PyPI distribution, and GHCR
  image all remained absent. Reapply the exact `0.3.1` version surfaces through this protected
  transition; its trusted-main merge is the sole authorized publication trigger.
- 2026-08-26: PR #86 passed its complete hosted matrix and was squash-merged as `3464348b`, but the
  GitHub Actions incident stranded its automatic release run `32985274926` before job creation.
  After GitHub reported recovery, normal cancellation, force-cancellation, rerun, and deletion all
  rejected the run's contradictory pre-queue state. Every `0.3.1` release surface remains absent.
  The protected recovery adds a positive monotonic release attempt: an exact increment retries the
  same final version, while decreases, skipped attempts, stale attempts on new versions, existing
  external state, and every untrusted trigger continue to fail closed.
- 2026-08-26: Implemented release attempt `2` with unit coverage for valid, invalid, decreasing,
  skipped, and stale transitions plus a real-Git integration test. The release guide and normative
  policy document the no-external-state precondition and retained concurrency/atomic-tag safety.
  Ruff formatting and linting, `ty`, the CI validator, 188 focused tests, 1,332 unit tests at 93.63%
  coverage, and 23 JavaScript tests pass. The canonical non-engine suite reached 1,524 passing tests
  but cannot complete locally because PostgreSQL and RustFS service variables are absent; hosted CI
  must verify those service-backed domains and the complete release matrix.
- 2026-08-26: Recovery PR #87 passed its complete hosted matrix and was squash-merged as
  `1b3d3a84`. Automatic release run `33008087510` published tag and GitHub Release `v0.3.1`, the
  `markweave==0.3.1` wheel and source distribution on PyPI, and the attested GHCR image at registry
  digest `sha256:3f50da7ef3664da6d73d4ad0cf0e9797f5a640f534114d179068cdc4c9f15a92`.
  The post-merge `main` matrix passed after a targeted rerun of one timing-sensitive SQLite lease
  test.
- 2026-08-26: Repinned the root Compose quickstarts and their static contract test to the published
  immutable `0.3.1` GHCR image identity. T23 can move to `Done` only after this final repin passes
  review, the required hosted matrix, and verification on `main`.
- 2026-08-26: PR #88 passed its required hosted matrix and independent exact-SHA review, then was
  squash-merged to `main` as `6e869b77`. Post-merge run `33011674248` passed every selected job,
  including the real Compose lifecycle and final gate, with the root quickstarts pinned to
  `ghcr.io/guillaume-lombardo/md-converter:0.3.1@sha256:3f50da7ef3664da6d73d4ad0cf0e9797f5a640f534114d179068cdc4c9f15a92`.
  All T23 acceptance criteria are verified on `main`; no limitation or exception remains.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
