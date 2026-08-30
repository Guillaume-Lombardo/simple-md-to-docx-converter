---
ticket: T43
linear_id: G1L-415
linear_url: https://linear.app/g1lom/issue/G1L-415/t43-decompose-persistence-adapters-by-responsibility
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T43 - Decompose persistence adapters by responsibility

## Objective

Split oversized job and template persistence adapters into cohesive repositories and query services without weakening cross-profile parity or transactional invariants.

## Acceptance criteria

* Separate job submission, claims/leases, terminal lifecycle, cleanup, template identity, versions/publication, search, retention, and audit persistence into bounded modules.
* Own and finalize `jobs/ports.py` and `templates/ports.py`; preserve their provider-neutral behavior or evolve signatures through an explicit compatibility layer removed in this ticket before T42 starts.
* Preserve atomicity, fencing, isolation, constraints, migrations, statement timeouts, S3/filesystem ordering, and safe errors.
* Avoid duplicated SQLite/PostgreSQL business rules and retain shared contract tests.
* Run unit, real SQLite/PostgreSQL/S3 integration, concurrency, downgrade/upgrade, and both-profile E2E tests.

## Dependencies

* T44
* T12
* T15

## Implementation boundary

* Own job/template persistence module decomposition, `jobs/ports.py`, `templates/ports.py`, and storage contract tests.
* Do not change HTTP, CLI, or worker behavior; T42 starts only after this port contract merges.

## Progress

* 2026-08-30: Started implementation on `refactor/T43-decompose-persistence` from verified `main` at `163bb697`. T12, T15, and T44 are `Done`; scope is limited to job/template persistence decomposition, finalized provider-neutral ports, and the corresponding contract, migration, concurrency, and profile-parity tests.
* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up assigned final ownership of job and template persistence ports to T43 before T42 begins.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
