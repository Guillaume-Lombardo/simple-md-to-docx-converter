---
ticket: T42
linear_id: G1L-416
linear_url: https://linear.app/g1lom/issue/G1L-416/t42-decompose-the-conversion-worker-orchestration
status: Backlog
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

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
