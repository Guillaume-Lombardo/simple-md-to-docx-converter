---
ticket: T49
linear_id: G1L-423
linear_url: https://linear.app/g1lom/issue/G1L-423/t49-remove-legacy-package-artifacts-and-enforce-namespace-cleanliness
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T49 - Remove legacy package artifacts and enforce namespace cleanliness

## Objective

Remove residual `md_converter` build/runtime artifacts and prevent the retired namespace or stale local release outputs from contaminating development and package verification.

## Acceptance criteria

* Remove ignored legacy bytecode trees and obsolete `dist/md_converter-*` artifacts from the maintained workspace using a documented safe cleanup path.
* Ensure clean source, sdist, wheel, editable install, and test environments cannot import the retired `md_converter` namespace.
* Add repository checks that detect unexpected source namespaces, tracked bytecode/build outputs, stale package names, and inconsistent version artifacts.
* Refresh durable orchestration notes to the current verified main state while preserving machine-local notes and unrelated user work.
* Do not remove the legacy environment-variable compatibility required until 1.0 or historical release evidence.

## Dependencies

* T22

## Implementation boundary

* Own safe legacy-artifact cleanup, namespace checks, package contamination tests, and durable orchestration-note refresh.
* Preserve historical release evidence and all 0.x legacy environment aliases.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
