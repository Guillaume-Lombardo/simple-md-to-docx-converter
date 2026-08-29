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

* Implement `markweave backup` and `restore` by reusing profile-neutral recovery services and existing scripts rather than duplicating storage logic.
* Require explicit profile, destination/source, bounded timeouts, integrity metadata, and confirmation for restore mutations; support deterministic non-interactive operation.
* Preserve standalone and distributed RPO/RTO evidence contracts, immutable reports, isolated restore verification, and readiness proof.
* Reject mixed configuration, unsafe paths, symlinks, incomplete backup sets, identity mismatch, concurrent destructive operations, and secret leakage.
* Cover both profiles with real storage integration and final rootless image E2E tests, including interrupted and failed restore behavior.

## Dependencies

* T31
* T39
* T12
* T18
* T20

## Implementation boundary

* Own recovery-facing CLI adapters and reusable backup/restore orchestration.
* Do not edit general runtime commands or container entrypoints.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.

