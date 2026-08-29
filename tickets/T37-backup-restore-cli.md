---
ticket: T37
linear_id: G1L-412
linear_url: https://linear.app/g1lom/issue/G1L-412/t37-add-backup-and-restore-cli-commands
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T37 - Add backup and restore CLI commands

## Objective

Expose the approved standalone and distributed backup/restore workflows through guarded operational CLI commands.

## Acceptance criteria

* Implement production `markweave backup` and `restore` services in the package; reuse profile-neutral recovery contracts, but never delegate production recovery to `scripts/run_restore_exercise.py`, destructive E2E helpers, or an operator-supplied shell string.
* For standalone, create one content-addressed, checksummed set from an SQLite online snapshot plus the complete stable object tree; restore only while offline into an empty destination, then verify identity/integrity before migration and readiness.
* For distributed, use typed explicitly configured PostgreSQL and S3-provider adapters, require a quiescence or provider consistency proof, and bind both recovery-point identities into one signed-or-checksummed manifest; fail closed if either side or proof is missing.
* Restore distributed sets only into isolated empty database and bucket targets, verify stable object references before migration/readiness, and never switch production traffic.
* Require explicit profile, destination/source, bounded timeouts, integrity metadata, and confirmation for restore mutations; support deterministic non-interactive operation.
* Preserve standalone and distributed RPO/RTO evidence contracts, immutable reports, isolated restore verification, and readiness proof; make the quarterly exercise consume the production commands.
* Reject mixed configuration, unsafe paths, symlinks, incomplete backup sets, identity mismatch, concurrent destructive operations, and secret leakage.
* Cover both profiles with real storage integration and final rootless image E2E tests, including interrupted and failed restore behavior.

## Dependencies

* T31
* T39
* T12
* T18
* T20

## Implementation boundary

* Own recovery-facing CLI adapters, provider-neutral backup/restore ports, the production standalone adapter, configured distributed provider adapters, manifests, and reusable orchestration.
* Do not edit general runtime commands or container entrypoints.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up fixed distinct production contracts for standalone and distributed backup sets and excluded test-only and arbitrary-shell recovery paths.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
