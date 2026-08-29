---
ticket: T48
linear_id: G1L-424
linear_url: https://linear.app/g1lom/issue/G1L-424/t48-expand-mutation-testing-across-critical-invariants
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T48 - Expand mutation testing across critical invariants

## Objective

Extend bounded mutation testing from observability to the security, authentication, archive, queue, worker, and persistence invariants most likely to fail silently.

## Acceptance criteria

* Define a reviewed risk-ranked mutation scope and deterministic per-domain commands with bounded runtime; the gate passes only when every selected non-equivalent mutant is killed, while each excluded/equivalent mutant requires a reviewed technical justification recorded in the artifact.
* Cover authentication/session versioning, origin/CSRF checks, archive/path/SVG validation, job leases/fencing/idempotency, retention, and storage integrity.
* Add or strengthen tests to kill relevant surviving mutants without asserting implementation trivia.
* Run affected mutation domains on pull requests and a broader bounded matrix on schedule, preserving one required gate and useful artifacts.
* Fail on surviving, untested, timed-out, or suspicious selected mutants; document reviewed exclusions with technical justification and track the exact killed/selected counts without weakening existing coverage thresholds.

## Dependencies

* T41
* T42
* T43
* T05
* T22

## Implementation boundary

* Own mutation configuration, CI scheduling, artifacts, and mutation-driven tests after target modules stabilize.
* Do not combine production refactors with mutation-test enablement.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up made zero surviving selected non-equivalent mutants the deterministic gate.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
