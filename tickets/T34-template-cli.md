---
ticket: T34
linear_id: G1L-410
linear_url: https://linear.app/g1lom/issue/G1L-410/t34-add-template-cli-commands
status: Backlog
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

* Own only template/preference CLI modules and their tests/documentation.
* Do not refactor template services or persistence in this ticket.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.

