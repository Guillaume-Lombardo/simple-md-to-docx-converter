---
ticket: T31
linear_id: G1L-406
linear_url: https://linear.app/g1lom/issue/G1L-406/t31-build-the-markweave-cli-foundation
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T31 - Build the Markweave CLI foundation

## Objective

Provide the installed `markweave` executable and a stable command framework shared by local operational commands and remote HTTP client commands.

## Acceptance criteria

* Declare a `markweave` console entry point while preserving the package import and `python -m markweave.runtime` compatibility during migration.
* Define stable command groups, help, version output, exit codes, stdout/stderr rules, JSON and human-readable output, timeouts, and non-interactive behavior.
* Keep password and secret values out of arguments, logs, tracebacks, shell completion, and process listings.
* Expose reusable typed command, output, configuration-profile, and error abstractions without importing every optional backend eagerly.
* Add unit, packaging, clean-wheel installation, and shell-invocation tests; document the CLI contract for downstream tickets.

## Dependencies

* T01
* T06
* T22

## Implementation boundary

* Own the new CLI framework, entry point, common output/errors, and CLI contract tests.
* Do not implement HTTP authentication, resource commands, operational commands, container changes, or configuration renaming.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.

