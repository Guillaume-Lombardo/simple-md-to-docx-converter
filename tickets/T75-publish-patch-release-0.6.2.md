---
ticket: T75
linear_id: G1L-574
linear_url: https://linear.app/g1lom/issue/G1L-574/t75-publish-patch-release-062
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T75 - Publish patch release 0.6.2

## Objective

Publish Markweave patch release 0.6.2 from the current verified main branch, packaging the
completed changes since 0.6.1 without expanding scope or claiming unfinished reverse-conversion or
Kubernetes qualification.

## Acceptance criteria

- Update the authoritative Python package and application version from 0.6.1 to 0.6.2 and reset
  the release attempt to 1.
- Keep the OpenAPI snapshot, lockfile, version contracts, README, and changelog consistent with the
  candidate version.
- Summarize user-visible and operational changes since 0.6.1, explicitly retaining experimental
  status for reverse conversion and the optional Kubernetes backend.
- Prove that 0.6.2 is absent from PyPI, GitHub tags/releases, and public container tags before
  publication.
- Pass applicable formatting, linting, type, package, OpenAPI, release-policy, frontend, and Python
  test gates.
- Merge only after required CI, independent review, and review conversations pass.
- Verify the automatic release reaches a terminal successful state and publishes the exact
  reviewed source identity.

## Dependencies

- T22
- T69
- T70
- T71
- T72

## Progress

- 2026-09-11: Created after confirming no existing issue covers the 0.6.2 release transition. Work
  started from verified `main` at `327a4c904484bd687145123d9783fb149e308792`; public PyPI 0.6.2
  and GitHub v0.6.2 identities are absent.
- 2026-09-11: Updated the authoritative package/application version, lockfile, generated OpenAPI
  snapshots, release tests, README, changelog, architecture, configuration, deployment, recovery,
  release, and documentation-index surfaces. The release artifact verifier now also installs the
  `kubernetes` extra in a clean Python 3.14 environment and checks the packaged Kubernetes attester
  entry point; this closes a packaging-test gap introduced by T74.
- 2026-09-11: `uv sync --all-groups`, Ruff formatting/linting, `ty`, `uv lock --check`, OpenAPI
  generation/checking, pnpm workspace validation, and the frontend browser helper suite pass. The
  focused package/release/documentation/CI set passes 245 tests, including real wheel/sdist builds
  and isolated installation of every supported extra. The canonical local selection ran 4,257
  tests: 4,206 passed with 94.58% application coverage; 44 PostgreSQL setup errors and three RustFS
  failures are exclusively due to absent `MARKWEAVE_TEST_POSTGRES_URL` and
  `MARKWEAVE_TEST_S3_*` services. Four unrelated environment/load failures passed when rerun
  independently. Hosted CI remains responsible for both configured storage-profile boundaries.
- 2026-09-11: CodeRabbit identified that module discovery alone did not execute the lazy
  `kubernetes.client` and `kubernetes.config` imports. The release verifier now imports every
  required Kubernetes runtime submodule, rejects an installed-but-unimportable dependency, and
  retains the server-dependency isolation check. The 55-test release-verifier set, including a
  real clean Kubernetes-extra installation and attester invocation, passes after the correction.
- 2026-09-11: PR #228 passed all 14 CI jobs and squash-merged to `main` as
  `d7188c4fd3d9c7d4f1d82995850b3827f09e3a83`. Automatic release run `34648944379` completed
  successfully and published the wheel, sdist, GitHub release/tag, SBOMs, attestations, and paired
  images.
- 2026-09-11: Anonymous GHCR verification matches the retained receipts. The backend digest is
  `sha256:30c9fa538e7c4eb56b1b1434cd14251ee82b3a3962ad1a072fe5564a118330ae`; the frontend digest is
  `sha256:8908a28dea630ef520eb60828d6717c46fdc445c8878e4bacc6b239ccb32c2e7`. Both attestations verify,
  and the public Compose and quickstart defaults now pin this exact pair.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or
progress changes.
