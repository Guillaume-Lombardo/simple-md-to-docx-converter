---
ticket: T36
linear_id: G1L-409
linear_url: https://linear.app/g1lom/issue/G1L-409/t36-add-runtime-worker-doctor-and-migration-cli-commands
status: In Progress
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

* Exclusively own T31's pre-registered `src/markweave/cli/commands/runtime.py` family, runtime-facing CLI adapters, diagnostics, migrations, package runtime compatibility, and their domain tests/documentation.
* Do not edit the root registry, shared help snapshots, documentation index, recovery-operations family, container/Compose entrypoints, or backup/restore workflows.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Final audit follow-up assigned the pre-registered runtime-operations family exclusively to T36.
* 2026-08-30: Implementation started from verified `main` after T31 and T39 completion. Work is isolated to the runtime operations family, its adapters, dedicated tests, and narrowly relevant documentation.
* 2026-08-30: Implemented supported `serve`, distributed `worker`, bounded redacted `doctor`, and profile-safe `migrate` commands. Runtime commands reuse the existing FastAPI and worker assembly; SQLite migration uses an immediate write reservation and PostgreSQL retains its transaction advisory lock.
* 2026-08-30: Added unit, real-process/filesystem/SQLite integration, concurrent migration, and final-image rootless tests. The exact T36 image passed `migrate`, idempotency, `doctor`, mixed-profile rejection, and standalone-worker rejection as arbitrary UID 53000 with a read-only root filesystem and no network.
* 2026-08-30: Ruff, `ty`, JavaScript tests, 74 focused CLI/runtime tests, and the 1,681-test locally runnable suite passed with 95.23% coverage. The canonical unfiltered suite was attempted; complete engine/font, PostgreSQL, and RustFS groups remain unavailable locally. The full container runner reached and passed the T36 smoke, then an existing API smoke failed because its ClamAV boundary returned 503; a fresh exact-image T36 smoke passed independently.
* 2026-08-30: PR light CI exposed that the dedicated runtime-command tests lacked the `unit` marker and were excluded from the exact CI coverage command. Registering that existing focused suite raised changed application coverage from 79.40% (212/267) to 95.13% (254/267); 1,475 unit tests pass and application branch coverage is 90.68% (1,692/1,866).

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
