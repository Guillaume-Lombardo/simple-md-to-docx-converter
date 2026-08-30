---
ticket: T34
linear_id: G1L-410
linear_url: https://linear.app/g1lom/issue/G1L-410/t34-add-template-cli-commands
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T34 - Add template CLI commands

## Objective

Expose visible-template discovery, preferences, ownership mutations, and immutable version operations through HTTP-only CLI commands.

## Acceptance criteria

* Implement template list/search/show/create/download/update/replace/archive/delete commands plus version list/download/restore.
* Implement preferred-template and administrator fallback-template commands with explicit owner and administrator behavior.
* Use ETag/If-Match deterministically, require explicit confirmation or force flags for destructive operations, and never bypass HTTP authorization.
* Write downloads atomically and preserve template identity, immutable ownership, version integrity, audit attribution, and safe errors.
* Cover two users and one administrator, stale ETags, archived templates, guarded deletion, hostile files, and both storage profiles through unit, integration, and final-image E2E tests.

## Dependencies

* T32
* T15

## Implementation boundary

* Own only T31's pre-registered template/preference family modules and domain tests/documentation; do not edit the root registry, shared help snapshots, documentation index, or other command families.
* Do not refactor template services or persistence in this ticket.

## Progress

* 2026-08-30: Anchored download temporary creation, publication, cleanup, and directory durability to one no-follow parent-directory descriptor, preventing parent replacement races from redirecting cleanup or leaving temporary files behind. Added deterministic parent-rename coverage, merged current `main` at `3546ce1` without rebasing, and revalidated 57 focused tests, 98% template-command branch coverage, and both complete final-image profiles.
* 2026-08-30: Closed the independent-review findings: no-clobber downloads now publish atomically without overwriting a target created during the write, with a deterministic race regression and temporary-file cleanup. Expanded the dedicated final-image matrix to cover a regular owner, a second user's mutation denial, administrator intervention without ownership transfer, non-administrator fallback denial, search, archived visibility, guarded deletion followed by successful deletion, and credential-safe setup in both standalone and distributed profiles. Ruff, ty, 56 focused unit/integration tests, 98% template-command branch coverage, and both complete final-image workflows pass.
* 2026-08-30: Implemented the complete HTTP-only template, immutable-version, preference, and administrator-fallback CLI family. Added deterministic ETag handling, guarded archive/delete operations, integrity-checked atomic downloads, hostile-file protections, real-HTTP three-actor coverage, and dedicated final-image coverage. Targeted checks and both standalone and distributed final-image workflows pass; the canonical local suite reached 95.46% overall coverage with only unavailable PostgreSQL/S3 service errors before those profiles were exercised successfully by the final-image harness.
* 2026-08-30: Started implementation on `feat/T34-template-cli` from verified `main` at `c1cae3b`. Scope remains limited to the pre-registered HTTP-only template, version, preference, and fallback CLI family plus its domain-specific tests and documentation.
* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up restricted this worker to T31's template family and excluded shared registry, help, and documentation-index files.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
