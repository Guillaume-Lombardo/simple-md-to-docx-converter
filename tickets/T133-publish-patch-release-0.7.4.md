---
ticket: T133
linear_id: G1L-686
linear_url: https://linear.app/g1lom/issue/G1L-686/t133-publish-patch-release-074-and-corrective-075
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T133 - Publish patch release 0.7.4 and corrective 0.7.5

## Objective

Publish Markweave 0.7.4, a patch release, at the owner's request (2026-10-11). The owner first
asked for a minor bump. The product specification reserves 0.8.0 for T93, which publishes it after
full Composer acceptance, so on 2026-10-11 the owner chose a 0.7.4 patch release instead and left
the specification unchanged. It ships the work merged since 0.7.3: among others the local
serverless CLI conversion (T106), Markdown packaging for remote conversion (T104), older-schema
recovery-set restore (T128), recorded engine versions aligned with the shipped toolchain (T129),
Compose profile consolidation (T99) and the Composer workspace race fix (#292).

The version transition is part of the ticket-closure pull request that also closes the T99, T106,
T128 and T129 mirrors.

## Acceptance criteria

- `project.version`, the application version source and every version-bearing fixture, test and
  OpenAPI document move from 0.7.3 to 0.7.4 following `docs/releasing.md`;
  `tool.markweave.release.attempt` is reset to `1`.
- `CHANGELOG.md` and `docs/upgrading.md` describe 0.7.4, including the minimum restorable
  recovery-set revision `20260829_14` from T128 and any Compose profile migration from T99.
- The pull request passes the complete `CI / gate` and an independent review, then is
  squash-merged to `main`.
- The automatic release on `main` reaches a terminal result. PyPI 0.7.4 and the three GHCR images
  are published with their receipts.
- A follow-up pull request adopts the exact published GHCR receipt digests in Compose and completes
  any release-owned quickstart command migration. No unrelated work is integrated while the
  published PyPI version and the public Compose pin differ.

## Corrective 0.7.5 release (owner decision 2026-10-12)

The 0.7.4 release run 38182704382 published only the Python package, tag and GitHub Release. The
frontend image failed the Critical vulnerability gate on `next` 16.3.4 (GHSA-vcvr-r3jv-pc5j, fixed
in 16.3.6) before any registry mutation, so container-only recovery is impossible. The owner chose
a corrective 0.7.5 release:

- Upgrade `next` to at least 16.3.8, along with the other frontend dependencies that have an
  available fix for a High or Critical advisory, within compatible ranges.
- Add a reviewed, tested, one-time `public_alignment` exception for the exact Python-only partial
  0.7.4 to 0.7.5 transition, modelled on the 0.7.2 to 0.7.3 exception.
- Record the 0.7.4 incident in the release history.
- Move the version to 0.7.5 following `docs/releasing.md`.
- After the protected merge, monitor the release. On success, adopt the exact 0.7.5 GHCR receipt
  digests in Compose in the follow-up PR.

## Dependencies

- T106, T128 and T129 verified on `main`.

## Blocking points

- None known. Do not pre-pin an unpublished image or infer its registry digest before publication
  evidence exists.

## Progress

- 2026-10-11: Created in Linear and mirrored here. Dependencies verified on `main`: T129 by main
  CI run 38156378579 at `ed03bdf`, T128 by main CI run 38158819217 at `c3da5a5`, and T106 by
  main CI run 38164507559 at `4280275`; T99 by main CI run 38153540913 at `a087eb5`. PR #293
  opened from `chore/T133-release-0.8.0` (based on `main` `4280275`) with the ticket closures and
  a first preparation for minor release 0.8.0; its CI run 38168181317 passed all 19 jobs.
- 2026-10-11: Owner decision. The minor bump was requested first, but the specification reserves
  0.8.0 for T93 after full Composer acceptance. The owner chose patch release 0.7.4 and left the
  specification and T93 unchanged. PR #293 is retargeted to 0.7.4 with new commits on the same
  branch, whose name keeps `0.8.0` only for historical reasons. `project.version`,
  `src/markweave/version.py`, `uv.lock`, both OpenAPI documents and the release and version tests
  move from 0.7.3 to 0.7.4; `tool.markweave.release.attempt` is already `1`. `CHANGELOG.md` and the
  "Upgrading to 0.7.4" section of `docs/upgrading.md` record the restore floor `20260829_14`, the
  removed `compose.nextjs*.yaml` files and the Composer migrations, and state that Composer ships
  disabled by default with its complete acceptance and the 0.8.0 minor release still pending.
  Compose, the quickstart helpers and the published-image documentation stay pinned to the
  verified 0.7.3 receipts, as the public-alignment version transition requires, until the
  adoption follow-up. No publication has occurred.
- 2026-10-12: PR #293 was squash-merged as `5d0ac0bb329018fa3a88d7ba516e575de9abc100`; main CI
  run 38182704347 succeeded. Release run 38182704382 then completed `detect`, `build-and-verify`,
  `create-release` and `publish`: PyPI `markweave` 0.7.4 is published and tag `v0.7.4` and its
  GitHub Release (no assets) target that source. Job `container / build-and-publish`
  (114598666127) failed at "Generate and verify release SBOM evidence for all three images" with
  `error: fixable Critical vulnerabilities found`: the backend image passed and the frontend image
  `md-converter-web` failed on `next` 16.3.4 (GHSA-vcvr-r3jv-pc5j). The failure happened before
  staging retention and any registry mutation; the run retained only artifact 11718675473
  (`python-release-v0.7.4`), and GHCR has no `0.7.4` tag in any of the three repositories. The
  container-only recovery path is impossible. The incident is recorded in
  `docs/evidence/release-migration-history.md`.
- 2026-10-12: Owner decision: corrective release 0.7.5 in one pull request. Branch
  `fix/T133-release-0.7.5` upgrades `next` and `eslint-config-next` to 16.3.8 and moves `sharp`,
  `undici`, `brace-expansion`, `js-yaml` and `source-map-js` to fixed releases (a scoped
  `brace-expansion@5` override and the bumped `js-yaml` override in `pnpm-workspace.yaml`). The
  pinned Grype 0.116.1 reports no High or Critical fixable finding over `pnpm-lock.yaml` and `web/`;
  the only remaining fixable findings are Low ones in the untouched `toolchain/document-engines`
  npm lock. A locally built frontend image passed the rootless smoke and
  `scripts/container/supply-chain.sh` in release mode. `scripts/release/public_alignment.py` adds a
  one-time exception for the exact partial 0.7.4 to 0.7.5 transition, bound to the source, failed
  run, Python artifact ID, name and digest, asset-free Release and GHCR tag absence; the 0.7.2
  exception is unchanged. The version moves to 0.7.5 with release attempt 1; Compose and the
  quickstarts stay on the verified 0.7.3 receipts until the 0.7.5 adoption follow-up.
- 2026-10-12: Independent review round 1 of PR #295 at `a98a120` found that hosted CI run
  38215120037 failed `CI / container` (job 114698006421) and `CI / e2e-distributed` (job
  114698006523) at the backend RPM inventory gate: the build produced
  `92063803ffaf534054d2d198abd814d08f22626a4238cb126e03376fb102a99e` instead of the reviewed
  `2b02ce10a0d8955ff1817b56323d6dd7e091c1d1316b11700e0f87d24319dc90`. The inventory extracted
  from the main `00a98ec` backend image reproduces the reviewed digest. A cache-free local backend
  build reproduces the hosted digest, and the only change is `curl-minimal`, `libcurl-minimal` and
  `libcurl-devel` moving from `0:7.76.1-40.el9_8.7` to `0:7.76.1-40.el9_8.8`. All other 546 rows,
  names, architectures and licenses are identical. The packages carry the Red Hat `fd431d51`
  RSA/SHA256 signature, and their changelog records the fixes for CVE-2026-8924 and
  CVE-2026-11856. The reviewed inventory digest, its Containerfile comment and the fail-closed
  contract test are refreshed; the gate stays strict, and the base image, engine and public image
  pins are unchanged. The frontend and reverse-attempt images have no RPM inventory pin. Locally,
  the backend image passed the final-image, API, distributed API and recovery CLI smokes and the
  release supply-chain gate. The reverse-attempt image passed its smoke and the reverse-attempt
  release gate. Neither has a fixable Critical finding. Hosted container and E2E jobs must pass
  before merge.
- 2026-10-12: PR #295 was squash-merged as `d54a9c5f9440205bc415e7a49c4583f502392c29`. Release
  run 38223418529 succeeded in every job (`detect`, `build-and-verify`, `create-release`,
  `publish`, `container / build-and-publish`, `container / attest` and
  `container / release-evidence`; `container / recover-evidence` was skipped). PyPI `markweave`
  0.7.5 carries the wheel and sdist with PEP 740 attestations from `release.yml` in the `pypi`
  environment. Tag `v0.7.5` and its non-draft GitHub Release target that source, and the Release
  carries `release-images.json`, `release-images.sha256` and every role's receipt, metadata, SBOMs,
  vulnerability reports and bundle checksums. `release-images.json` (schema 2) binds the three
  roles to version 0.7.5, that source and frontend lockfile digest
  `9c40e366fdf12532ba3f0d64a6172a55c633fd046c53a9739ff5d40da9a8c7f1`. The published receipts are
  backend `ghcr.io/guillaume-lombardo/md-converter`
  `sha256:237597e5aee89a744f29027e3ad0e10845296f435da8902d1ee5af2b7f591677`, frontend
  `ghcr.io/guillaume-lombardo/md-converter-web`
  `sha256:f628055556c61b04cfbd3202e32464ad38719d7b057ea87ed9b249e44c6d01ce` and reverse attempt
  `ghcr.io/guillaume-lombardo/md-converter-reverse-attempt`
  `sha256:104d35de4f75459ed5bab4583a5fc472d521247cc23a339ed9b8995296ec97bc`. Anonymous GHCR
  requests for the `0.7.5` and source tags and for each digest returned these exact digests, and
  each digest has SLSA provenance from `container-release.yml` on `main` at that source.
- 2026-10-12: Main CI run 38223418603 for `d54a9c5` failed on attempt 1 only in
  `CI / e2e-distributed`, on the known scanner-outage readiness timeouts (`frontend_alias` and
  `backend_ready` `TimeoutError`, tracked by T130). The failed job was rerun. Attempt 2 completed with conclusion `failure`: in `CI / e2e-distributed`
  (job 114743365865) the scanner-outage routes all returned 200, but the
  `browser-next-reversion` structured-PPTX step timed out after 30 seconds waiting for the
  `PPTX preview` text after source capture in Composer (`TimeoutError`,
  `tests/e2e/browser-next-reversion.test.mjs:124`), and `CI / gate` failed as a consequence. Every
  other job passed. The failure is in the hosted E2E harness after the release run had already
  published; it is not the T130 signature and needs its own triage.
- 2026-10-12: Branch `chore/T133-adopt-0.7.5-images` adopts the verified backend and frontend
  receipts in `compose.yaml`, both quickstart helpers and the Podman insecure E2E runner, records
  the reverse-attempt digest for the native-broker deployment, and records the publication in the
  release history and the published-image documentation. `scripts.release.public_alignment`
  reports `aligned` for the branch with both the `pull_request` and `schedule` events. The
  repository `release_pair verify` helper accepted the retained `container-release-v0.7.5`
  artifact against those digests, the source and the frontend lockfile digest. Against the
  anonymously pulled 0.7.5 images, the local simple Docker, simple rootless Podman and Podman
  insecure quickstart E2E runners passed and left no container, volume or network behind. The
  secure Docker runner could not run locally because its loop-device setup needs an interactive
  `sudo` password; hosted CI covers that path. Status stays In Progress until the adoption is
  verified on `main`.
- 2026-10-12: Done. The corrective 0.7.5 release published PyPI, the tag, the GitHub Release and the three GHCR images; the receipt adoption merged as `e37e7b0` (PR #296) and main CI passed. docker-box stays on its previous images until the owner approves an update.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-686.
