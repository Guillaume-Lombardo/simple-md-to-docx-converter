---
ticket: T79
linear_id: G1L-578
linear_url: https://linear.app/g1lom/issue/G1L-578/t79-decompose-large-reverse-conversion-modules-by-responsibility
status: In Progress
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

## Synchronization

Keep Linear and this mirror synchronized. Mark Done only after verification on main.
