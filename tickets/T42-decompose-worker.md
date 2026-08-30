---
ticket: T42
linear_id: G1L-416
linear_url: https://linear.app/g1lom/issue/G1L-416/t42-decompose-the-conversion-worker-orchestration
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T42 - Decompose the conversion worker orchestration

## Objective

Split worker claim, heartbeat, processing, publication, cancellation, recovery, and cleanup responsibilities while preserving the durable job state machine.

## Acceptance criteria

* Reduce `JobWorker.run_once` to an explicit orchestration flow with separately testable claim, execution, heartbeat, publication, and failure services.
* Preserve leases, fencing, idempotency, cancellation, timeouts, process termination, retry, recovery, template freezing, integrity, and observability semantics.
* Keep one implementation shared by embedded and external workers and avoid storage-product logic in domain services.
* Add deterministic concurrency and failure-injection tests plus real SQLite/PostgreSQL/S3 and final-image recovery coverage.
* Do not change public job states, error codes, manifests, or HTTP behavior.

## Dependencies

* T13
* T21
* T43

## Implementation boundary

* Own `jobs/worker.py` decomposition and new worker orchestration modules/tests after T43 finalizes persistence ports.
* Do not edit HTTP routers, CLI modules, persistence layout, `jobs/ports.py`, or `templates/ports.py`; consume the finalized port contract and open a separately synchronized defect if it is insufficient.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up serialized T42 after T43 and prohibited concurrent edits to persistence ports.
* 2026-08-30: Implementation started on `refactor/T42-decompose-worker` from exact `main` SHA `c1cae3b6`. The work owns worker orchestration decomposition and focused tests while preserving finalized persistence ports, public contracts, and both embedded and external worker behavior.
* 2026-08-30: Decomposed claim, heartbeat, execution, failure resolution, fenced publication, recovery, and cleanup into independently tested services while retaining `ConversionWorker` as the shared embedded/external composition root. Historical and focused worker tests pass, real SQLite/PostgreSQL/S3 integrations pass, both final-image recovery profiles pass, and changed worker modules retain 99–100% coverage. The full host suite reached 1,983 passes and 95.99% total coverage; 37 document-engine checks require the pinned final-image engines and one Argon2 timing median was load-sensitive. A baseline zero-byte E2E restart artifact was isolated outside T42 and synchronized separately as T52 / G1L-459.
* 2026-08-30: Merged exact current `main` SHA `7850ab69` without rebasing; its delta contained only verified T24/T27 ticket closure notes. Post-merge Ruff formatting and lint, `ty`, 62 focused worker tests, and 25 worker assembly/runtime/SQLite tests pass. The T42 topology remains clean and excludes T52.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
