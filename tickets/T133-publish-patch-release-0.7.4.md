---
ticket: T133
linear_id: G1L-686
linear_url: https://linear.app/g1lom/issue/G1L-686/t133-publish-patch-release-074-and-corrective-075
status: In Progress
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

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-686.
