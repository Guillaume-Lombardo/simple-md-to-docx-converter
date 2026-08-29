---
ticket: T36
linear_id: G1L-409
linear_url: https://linear.app/g1lom/issue/G1L-409/t36-add-runtime-worker-doctor-and-migration-cli-commands
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T36 - Add runtime, worker, doctor, and migration CLI commands

## Objective

Replace package-internal runtime invocation with supported operational CLI commands while preserving the same application and worker implementations.

## Acceptance criteria

* Implement `markweave serve`, `worker`, `doctor`, and `migrate` with explicit standalone/distributed validation.
* Make `serve` and `worker` call the existing runtime assembly rather than duplicate application or job logic.
* Make `doctor` perform bounded non-mutating checks for configuration coherence, engines, fonts, scanner, storage, permissions, and runtime prerequisites with redacted output.
* Make `migrate` explicit, concurrency-safe, idempotent, observable, and unsuitable for accidental mixed-profile use.
* Preserve signals, exit codes, arbitrary UID, read-only root filesystem, health behavior, and add unit, real-boundary integration, and final-image tests.

## Dependencies

* T31
* T39
* T12
* T20

## Implementation boundary

* Own runtime-facing CLI adapters, diagnostics, migrations, and package runtime compatibility.
* Do not edit container/Compose entrypoints or backup/restore workflows.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
