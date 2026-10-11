---
ticket: T133
linear_id: G1L-686
linear_url: https://linear.app/g1lom/issue/G1L-686/t133-publish-patch-release-074
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T133 - Publish patch release 0.7.4

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

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-686.
