---
ticket: T76
linear_id: G1L-575
linear_url: https://linear.app/g1lom/issue/G1L-575/t76-rename-workflow-tabs-and-publish-patch-release-063
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T76 - Rename workflow tabs and publish patch release 0.6.3

## Objective

Publish patch release 0.6.3 with the tested T71/T72 fixes and the requested navigation
labels: `md 2 docx`, `x 2 md`, and `template docx`.

## Acceptance criteria

- Rename navigation labels while preserving routes, independent uploads, authorization,
  active/disabled states, and the accessible Experimental badge.
- Update navigation tests and user documentation.
- Bump the package/application version, lockfile, OpenAPI snapshots, version contracts,
  and changelog to 0.6.3, with release attempt 1.
- Include T71 certificate-pin environment decoding and T72 polling, unavailable-state,
  and upload-isolation corrections already tested on the deployed instance.
- Pass required CI and independent review, squash-merge the reviewed PR, and clean its branches.
- Monitor automatic publication, verify PyPI/GitHub/GHCR evidence, then adopt the exact
  published image pair through a follow-up reviewed PR.
- Preserve experimental reverse-conversion status and leave unrelated qualification work unchanged.

## Dependencies

- T22
- T71
- T72
- T75

## Progress

- 2026-09-19: User explicitly authorized the labels, patch bump, PR monitoring, squash merge,
  and cleanup. Continue the isolated `fix/T72-revert-upload-state` worktree from main `9799f31`.
  PyPI 0.6.3, GitHub release v0.6.3, and its Git tag are absent. Keep the verified 0.6.2
  Compose pair until the new publication receipts are available.

- 2026-09-19: Renamed visible/accessible navigation labels and updated component/E2E selectors,
  product wording and frontend documentation. The package, application, lockfile, OpenAPI snapshots,
  release tests and changelog now use 0.6.3; attempt remains 1. Both anonymous GHCR tags return
  bounded `MANIFEST_UNKNOWN` responses. Published Compose pins remain unchanged until adoption.
- 2026-09-19: Frontend checks pass all 207 tests at 90.32% branch coverage; the root browser-helper
  suite passes 16 tests. All 343 targeted configuration/version/release/documentation tests pass,
  including real package build/install checks. Ruff, ty and OpenAPI validation pass. The earlier
  canonical Python run and its unavailable PostgreSQL/S3 limitations are recorded in T71/T72;
  hosted exact-head CI must validate both storage profiles before merge.

- 2026-09-19: CI run 35452514475 found a changed UBI RPM inventory. Compared the retained
  baseline (`3c4d1883b398ebf8b2bdaa3e5fb9ff956214e395b6517e00f1e58f0903a49576`) with the
  actual CI transaction: only `mesa-libgbm`, `mesa-filesystem`, and `mesa-dri-drivers` advance
  from `25.2.7-4.el9` to `25.2.7-5.el9_8`. Applying exactly those three version substitutions
  reproduces the CI inventory digest
  `5062777d84d38c9d70c8a52c11b84c5e082fc652ec70e2d3255721a00ce031ef`; licenses, package
  names, architectures and all other versions are unchanged. Refreshed the fail-closed digest
  without disabling inventory validation, signature checks or vulnerability gates. Also added
  the existing completed T75 prerequisite to the delivery table as requested by CodeRabbit.

- 2026-09-19: PR #232 squash-merged as `5e789c11600d429997c037b4d24eea399821a2ed`.
  Exact-head CI run 35453205924 and main CI run 35454910111 pass every required domain,
  including both final-image E2E and storage profiles. The source branch is deleted locally
  and remotely. CodeRabbit's independent review finding was corrected; all conversations resolved.
- 2026-09-19: Automatic release run 35454910249 succeeds, publishing the PyPI wheel/sdist,
  GitHub tag/release and both GHCR images. Anonymous manifest checks match the retained receipts:
  backend `sha256:6560d86e4ca33327a5f454530c8b6a2fadb20b55af6373aea01378b00920bb4e`;
  frontend `sha256:bb60b8b72259d738c4eb93c29cf54aef9a8cf2f875e5851ebdc063ded5c99fa5`.
  Receipt hashes and the frontend lockfile match the paired release manifest. GitHub attestation
  verification enforces the repository, reusable container workflow, main ref, source SHA and
  hosted runner identity for both images. The follow-up adoption updates Compose, quickstarts,
  deployment documentation and their contracts; its protected merge remains pending.

- 2026-09-19: Adoption PR #234 passed every selected domain and `CI / gate` in run
  35456711142, including Compose quickstarts and both final-image E2E profiles. CodeRabbit
  completed its independent review without actionable findings. The PR squash-merged as
  `81fb51efce643e3149bbe4bcc19f7acab80159b5`; its exact source branch is deleted locally
  and remotely. Public release alignment passes from that main checkout: package, PyPI,
  GitHub receipts and both Compose image digests agree on 0.6.3. All T76 acceptance criteria
  are verified on main. Updating the separate test server remains dependent on restoring
  its forwarded SSH agent and is not claimed as part of the published-image adoption.

## Synchronization

Update this file and Linear whenever scope, status, acceptance criteria, dependencies,
implementation boundaries, or progress changes. Mark Done only after verification on main.
