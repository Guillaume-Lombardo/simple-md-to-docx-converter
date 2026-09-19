---
ticket: T79
linear_id: G1L-578
linear_url: https://linear.app/g1lom/issue/G1L-578/t79-decompose-large-reverse-conversion-modules-by-responsibility
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T79 - Decompose large reverse-conversion modules by responsibility

## Objective

Reduce maintenance cost in reverse-job persistence, broker runtime/transport implementations, and anydoc compatibility without changing their contracts.

## Acceptance criteria

- Inventory responsibilities and propose bounded extraction steps for persistence/reversion_jobs/repository.py, broker runtime/transport modules, and reversions/_anydoc_compat.py.
- Keep transaction, lease, termination-proof, authorization, resource-limit and deterministic-output invariants unchanged.
- Preserve one anydoc compatibility boundary and its license/private-symbol inventory; do not create a second parser or remove the adapter before an upstream replacement is verified.
- Deliver cohesive, independently reviewable changes with existing contract, security, integration and final-image E2E coverage on both storage profiles.
- Keep coverage thresholds and public imports/API compatibility intact; avoid arbitrary line-count targets.

## Dependencies

- T70
- T71

## Progress

- 2026-09-19: Created from the repository audit. Refactoring is planned separately from the cleanup.

- 2026-09-19: Started bounded responsibility extractions. Admission/owner reads and retention
  move to SQL mixins, preserving complete transaction blocks; claims, reconciliation, lease
  fencing, termination proof, and publication stay together in the repository. Podman workspace
  TAR serialization moves out of lifecycle control, and canonical mTLS control frames move out
  of socket/authentication orchestration. Pure hyperlink validation moves out of the anydoc
  adapter; all concrete model access, upstream renderer behavior, private-symbol inventory,
  and the MIT notice remain in the single compatibility boundary. Public imports remain stable.

- 2026-09-20: Completed through [PR #240](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/240), squash-merged as `88d747af79b2f45a02b2e6621444014acf6619e9` and verified on `main`. Admission/owner reads and retention now have dedicated SQL mixins; bounded Podman TAR and canonical mTLS control codecs are separate from lifecycle/authentication orchestration. Pure hyperlink validation is separate from the single anydoc adapter. Public imports, complete transaction bodies, lease/proof/authorization/resource limits, deterministic output, pinned engine, private-symbol inventory, and license remain unchanged. The responsibility inventory is in `docs/reverse-module-responsibilities.md`.
- 2026-09-20: Final reviewed head `7bf7cfde4cb4ebf98f81741622bfc75da0f7d1cf` passed [protected CI run 35474156430](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35474156430), including light/coverage, real broker/container, document engines, functional, both storage profiles, and both final-image rootless E2E profiles. Ruff formatting/lint, `ty`, OpenAPI artifact/compatibility, and diff checks passed. Independent initial and final-delta reviews found no actionable issues; the final T79 diff was byte-identical to the initial reviewed delta.
- 2026-09-20: The complete canonical default command passed on the final integrated candidate: **4,339 passed, 45 engine-marked deselected, 11 warnings**. Coverage was **94.89% overall, 91.22% application branches (6,265/6,868), and 98.41% changed executable lines (371/377)**. Focused final integration/parity checks passed **223 tests**; the exclusive real-broker/factory run passed **176 tests**. The entire merged tree matched the reviewed head, and **335 affected codec, SQLite, and anydoc parity tests passed again on main**.
- 2026-09-20: Earlier local checkout-mode and shared-broker-lock failures were superseded by the successful final canonical run. The engine-inclusive local command was not run because Pandoc, LibreOffice, Chromium, and Mermaid CLI are absent on the host; successful hosted engine and final-image domains provide that evidence. No integration/E2E or coverage exception was requested. Linear `G1L-578` was marked Done and re-fetched after main verification; local and remote source-branch deletion was verified, with a clean detached worktree and no remaining owned runtime process, managed unit, or held authority lock.

## Synchronization

Keep Linear and this mirror synchronized. Mark Done only after verification on main.
