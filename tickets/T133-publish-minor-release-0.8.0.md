---
ticket: T133
linear_id: G1L-686
linear_url: https://linear.app/g1lom/issue/G1L-686/t133-publish-minor-release-080
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T133 - Publish minor release 0.8.0

## Objective

Publish Markweave 0.8.0, a minor release, at the owner's request (2026-10-11). It ships the work
merged since 0.7.3: among others the local serverless CLI conversion (T106), Markdown packaging for
remote conversion (T104), older-schema recovery-set restore (T128), recorded engine versions
aligned with the shipped toolchain (T129), Compose profile consolidation (T99) and the Composer
workspace race fix (#292).

The version transition is part of the ticket-closure pull request that also closes the T99, T106,
T128 and T129 mirrors.

## Acceptance criteria

- `project.version`, the application version source and every version-bearing fixture, test and
  OpenAPI document move from 0.7.3 to 0.8.0 following `docs/releasing.md`;
  `tool.markweave.release.attempt` is reset to `1`.
- `CHANGELOG.md` and `docs/upgrading.md` describe 0.8.0, including the minimum restorable
  recovery-set revision `20260829_14` from T128 and any Compose profile migration from T99.
- The pull request passes the complete `CI / gate` and an independent review, then is
  squash-merged to `main`.
- The automatic release on `main` reaches a terminal result. PyPI 0.8.0 and the three GHCR images
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
  main CI run 38164507559 at `4280275`; T99 by main CI run 38153540913 at `a087eb5`. Release
  preparation started on `chore/T133-release-0.8.0` from `main` `4280275`: `project.version`,
  `src/markweave/version.py`, `uv.lock`, both OpenAPI documents and the release and version tests
  move to 0.8.0, and `tool.markweave.release.attempt` is already `1`. `CHANGELOG.md` and the
  0.8.0 section of `docs/upgrading.md` record the restore floor `20260829_14` and the removed
  `compose.nextjs*.yaml` files. Compose, the quickstart helpers and the published-image
  documentation stay pinned to the verified 0.7.3 receipts, as the public-alignment version
  transition requires, until the adoption follow-up. Open point: T93 (G1L-592) and the
  specification's Composer programme section still name 0.8.0 as T93's minor release; this
  release uses that number at the owner's request, so T93's release target needs an owner
  decision. No publication has occurred.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-686.
