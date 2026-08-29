---
ticket: T39
linear_id: G1L-417
linear_url: https://linear.app/g1lom/issue/G1L-417/t39-migrate-configuration-to-markweave-names
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T39 - Migrate configuration to MARKWEAVE names

## Objective

Adopt the Markweave brand for application configuration while preserving deterministic compatibility with legacy `MD_CONVERTER_*` names throughout 0.x.

## Acceptance criteria

* Introduce documented `MARKWEAVE_*` names for every public application setting, runtime host/port, cookie default, and applicable operator surface.
* Accept legacy `MD_CONVERTER_*` aliases for all 0.x releases; when both names are present with unequal values, fail closed without exposing values.
* Prefer only `MARKWEAVE_*` in new documentation, examples, Compose, quickstarts, tests, and emitted diagnostics; clearly mark legacy names deprecated until 1.0.
* Preserve secret handling, case rules, profile validation, existing deployments, and deterministic precedence for equal dual definitions.
* Test every alias class, conflicts, secrets, container/Compose propagation, and upgrade behavior.

## Dependencies

* T06
* T20
* T26

## Implementation boundary

* Own settings aliases, public environment names, Compose/quickstart propagation, and configuration migration documentation.
* Do not implement CLI commands, dependency extras, or unrelated runtime refactors.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.

