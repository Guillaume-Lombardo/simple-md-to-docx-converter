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

* 2026-08-30: Completed the responsibility split without changing public repository imports or HTTP/CLI/worker behavior. Job persistence is composed from submission, query, claim/lease, terminal-lifecycle, and cleanup stores; template persistence is composed from identity, search, publication, publication-recovery, immutable-version query, selection, and audit modules. The aggregate provider-neutral ports now inherit explicit bounded protocols, with no temporary compatibility layer.
* 2026-08-30: Addressed the independent review findings by separating template publication, interrupted-publication recovery, and immutable-version queries into distinct provider-neutral protocols. `get` now belongs only to the identity contract and implementation, and the architecture regression asserts the exact bounded method sets so the aggregate cannot absorb those responsibilities again.
* 2026-08-30: Diagnosed the final-image failures from retained traces rather than classifying them as intermittent. Template creation returned `TEMPLATE_ENGINE_TIMEOUT` at the constrained 15- and 30-second E2E deadlines, while privileged conversion access used an administrator session after the deliberate 60-second absolute lifetime. The E2E harness now gives document-engine activation a bounded 120-second deadline and renews short-lived browser sessions at explicit workload boundaries. Complete standalone and distributed final-image workflows both pass on exact head `5b340a6`, including browser, restart, recovery, checkpoint, restore, readiness, and disabled-origin checks; durable logs record exit code zero and the tested SHA for each profile.
* 2026-08-30: Validation after integrating T36, T47, and T37 passes Ruff formatting and linting, `ty`, 43 focused SQLite/architecture tests, 30 real PostgreSQL 18/RustFS integration tests, and 103 focused T43/T37 recovery compatibility tests. The pre-T37 merged head passed 1,585 unit tests with 94.36% coverage and the 1,834-test canonical non-document-engine matrix with 95.96% coverage; the T43 source and E2E harness blobs are bit-identical after the conflict-free T37 merge. Local host document-engine executables remain unavailable, while both final images exercised them successfully.
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
