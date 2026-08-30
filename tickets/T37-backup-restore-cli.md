---
ticket: T37
linear_id: G1L-412
linear_url: https://linear.app/g1lom/issue/G1L-412/t37-add-backup-and-restore-cli-commands
status: In Progress
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

* Exclusively own T31's pre-registered `src/markweave/cli/commands/recovery.py` family, recovery-facing CLI adapters, provider-neutral backup/restore ports, the production standalone adapter, configured distributed provider adapters, manifests, reusable orchestration, and their domain tests/documentation.
* Do not edit the root registry, shared help snapshots, documentation index, runtime-operations family, general runtime commands, or container entrypoints.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up fixed distinct production contracts for standalone and distributed backup sets and excluded test-only and arbitrary-shell recovery paths.
* 2026-08-29: Final audit follow-up assigned the pre-registered recovery-operations family exclusively to T37.
* 2026-08-30: Implementation started on `feat/T37-backup-restore` from verified `main` after T31 and T39 completed; ownership is limited to the recovery command family, provider-neutral recovery services/adapters, dedicated tests, and narrowly relevant recovery documentation.
* 2026-08-30: Implemented guarded production backup and restore commands for both profiles, authenticated content-addressed manifests, online SQLite and repeatable-read PostgreSQL snapshots, stable filesystem/S3 object recovery, isolated-target validation, rollback cleanup, structured quarterly exercise delegation, and operator documentation.
* 2026-08-30: Integrated verified `main` at `e453a28` after T32 through a normal HTTPS fast-forward and passed 90 focused CLI compatibility tests without changing T32-owned files.
* 2026-08-30: Verified Ruff formatting/lint, ty, 1,565 unit tests with the required line and branch coverage, 1,810 default tests against real PostgreSQL 18 and RustFS, 23 browser tests, and standalone/distributed success and failure workflows in the rebuilt rootless final image. The unfiltered engine suite remains unavailable on the host because Pandoc, Mermaid/Chromium, and LibreOffice executables are not installed locally; those boundaries are covered by the final-image smoke test and their existing image jobs.
* 2026-08-30: Integrated verified `main` at `059ee925` after T44 and aligned recovery resource ownership with its lifecycle contract: every internally created S3 client and streamed response body is now closed on success, failure, and interruption, while recovery SQL engines retain their existing unconditional disposal. Ruff, ty, 1,570 unit tests with 90.03% application branch coverage, and real PostgreSQL 18/RustFS recovery and lifecycle integrations passed under strict resource-warning filters.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
