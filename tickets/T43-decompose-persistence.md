---
ticket: T43
linear_id: G1L-415
linear_url: https://linear.app/g1lom/issue/G1L-415/t43-decompose-persistence-adapters-by-responsibility
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T43 - Decompose persistence adapters by responsibility

## Objective

Split oversized job and template persistence adapters into cohesive repositories and query services without weakening cross-profile parity or transactional invariants.

## Acceptance criteria

* Separate job submission, claims/leases, terminal lifecycle, cleanup, template identity, versions/publication, search, retention, and audit persistence into bounded modules.
* Preserve the existing ports and provider-neutral contract or evolve them through an explicit compatibility layer removed in the same ticket.
* Preserve atomicity, fencing, isolation, constraints, migrations, statement timeouts, S3/filesystem ordering, and safe errors.
* Avoid duplicated SQLite/PostgreSQL business rules and retain shared contract tests.
* Run unit, real SQLite/PostgreSQL/S3 integration, concurrency, downgrade/upgrade, and both-profile E2E tests.

## Dependencies

* T44
* T12
* T15

## Implementation boundary

* Own job/template persistence module decomposition and storage contract tests.
* Do not change HTTP, CLI, or worker behavior; coordinate any shared port change before implementation.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
