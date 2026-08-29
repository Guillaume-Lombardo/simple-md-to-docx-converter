---
ticket: T47
linear_id: G1L-419
linear_url: https://linear.app/g1lom/issue/G1L-419/t47-add-changelog-and-upgrade-compatibility-guidance
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T47 - Add changelog and upgrade compatibility guidance

## Objective

Provide concise release history and a deterministic operator upgrade/rollback contract across package, container, configuration, and database changes.

## Acceptance criteria

* Add a maintained changelog focused on user-visible, operational, security, deprecation, and compatibility changes rather than ticket implementation detail.
* Document supported upgrade paths, schema migration ordering, backup prerequisites, rollback limitations, container/package version alignment, and 0.x compatibility policy.
* Document `MD_CONVERTER_*` to `MARKWEAVE_*` migration and removal at 1.0 without inventing other unresolved product values.
* Integrate changelog checks into the release process so a material version transition cannot omit its entry.
* Link upgrade guidance from README, deployment, configuration, recovery, and release documentation.

## Dependencies

* T39
* T22
* T23

## Implementation boundary

* Own changelog, upgrade/migration guidance, and release checks for required entries.
* Do not select a release version or implement configuration aliases.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
