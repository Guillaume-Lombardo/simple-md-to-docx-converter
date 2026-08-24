# Resource policy and cleanup

T18 makes every production quota and resource ceiling explicit. The application has no built-in
production values for these settings: operators must size them from their workload and the T00
measurements, then provide them through `MD_CONVERTER_*` environment variables.

## Admission

`MD_CONVERTER_JOB_ACTIVE_LIMIT_PER_USER` bounds each owner's jobs in `queued` or `running` state.
`MD_CONVERTER_JOB_GLOBAL_QUEUE_CAPACITY` bounds the global persistent workload in `queued` or
`running` state. Counting both states prevents a lease recovery from requeueing work beyond the
configured capacity. Admission and job reservation are one database transaction. SQLite serializes the write transaction; PostgreSQL takes a
transaction-scoped advisory lock before counting and inserting. This prevents concurrent requests
from overshooting either limit. An exact idempotent replay is resolved before the counts, so retrying
an accepted request does not fail merely because the queue later became full.

The assembled HTTP adapter translates `JobUserQuotaExceededError` to `429` and
`JobQueueCapacityExceededError` to `503`. Both responses carry the configured
`MD_CONVERTER_CONVERSION_RETRY_AFTER_SECONDS` value in `Retry-After`; their stable error envelopes
contain no database or document details. Exact idempotent replays remain accepted after saturation.

## Document and worker budgets

The required upload, decompressed-byte, file, image, and diagram ceilings are assembled as a
`DocumentResourceBudget` with typed `ArchiveResourceBudget` and `DiagramResourceBudget`
projections. Upload and decompressed limits are independent because standalone Markdown and archive
inputs apply them at different boundaries. The archive and Mermaid processors constrain their
existing finer-grained validated limits with these shared projections before extraction or
rendering. Adapter-specific limits remain independently strict and cannot be widened by T18.

`MD_CONVERTER_JOB_MAX_DURATION_SECONDS` is converted into a monotonic `JobExecutionBudget` for each
claim. The processor receives a callable cancellation probe exposing that budget, including its
monotonic deadline and remaining duration. A durable user cancellation wins over duration
exhaustion; duration exhaustion wins over a simultaneous functional or unexpected processor error
and produces the stable `resource_budget_exceeded` failure. Lease loss remains an infrastructure
failure and wins before terminal transitions. Existing subprocess deadlines remain responsible for
terminating engine process groups. `FrozenTemplateJobProcessor` passes the absolute monotonic
deadline explicitly to the document processor, and Pandoc, Mermaid, and LibreOffice cap each
configured subprocess deadline by the remaining overall job duration when the engine starts.

Memory and disk-backed workspace ceilings are required configuration
(`MD_CONVERTER_WORKER_MEMORY_BUDGET_BYTES` and
`MD_CONVERTER_WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES`). Python cannot securely impose container
cgroups or Kubernetes ephemeral-storage limits on itself; T20 must apply these exact values to the
final rootless container and worker deployment. They are still validated and exposed through
`ResourceBudget` so deployment assembly has one typed source.

## Retention, recovery, and periodic cleanup

`MD_CONVERTER_JOB_RESULT_RETENTION_SECONDS` is the retained job-artifact window used for private
source and result objects. Terminal rows become `expired` in deterministic `(expires_at, id)` order.
Cleanup claims are leased and fenced, object deletion is idempotent, and the metadata acknowledgement
occurs only after every source and attempt-specific result key has been deleted. A crash therefore
leaves the candidate retryable after its cleanup lease expires.

The worker recovers expired execution leases and incomplete source reservations continuously.
Cleanup uses elapsed monotonic time, not processed-job count, with a required interval and bounded
batch size. Transient database or object-store failures use the configured backoff and remain
retryable. Production external and embedded worker factories inject the same assembled retention
service. The next cleanup deadline advances before cleanup begins, so a failed maintenance attempt
uses error backoff without causing an accelerated retry loop.

Superseded template versions are retained for 365 days. Bounded cleanup never claims the current
version, the ten newest published versions, or a version referenced by retained job metadata.
Claims use expiring fencing tokens. The object is deleted first and metadata is acknowledged only
by the claim owner, so a crash remains retryable. Audit rows cannot be updated. After 365 days they
are deleted in bounded transactions that create content-free cleanup reports which cannot be
updated or deleted.

Every conversion and template upload is scanned by ClamAV after its bounded read and before any
validation, processing, database reservation, or object-store write. The adapter uses clamd
`INSTREAM` over TCP directly from memory and creates no application temporary scan material.
`FOUND` returns stable `UPLOAD_MALWARE_DETECTED` with HTTP 422. Connection, timeout, scanner error,
oversized response, noncanonical or unterminated record, multiple records, trailing data, or other
indeterminate protocol output fails closed with stable
`UPLOAD_SCANNER_UNAVAILABLE` and HTTP 503. No durable quarantine is retained.

All implemented numeric ceilings and schedules remain required operator-supplied configuration;
this repository deliberately supplies no implicit production values.

## Required settings

In addition to the previously required conversion upload, request, retry, and result-retention
settings, T18 requires:

- `MD_CONVERTER_CONVERSION_MAX_DECOMPRESSED_BYTES`
- `MD_CONVERTER_CONVERSION_MAX_FILES`
- `MD_CONVERTER_CONVERSION_MAX_IMAGES`
- `MD_CONVERTER_CONVERSION_MAX_DIAGRAMS`
- `MD_CONVERTER_JOB_ACTIVE_LIMIT_PER_USER`
- `MD_CONVERTER_JOB_GLOBAL_QUEUE_CAPACITY`
- `MD_CONVERTER_JOB_MAX_DURATION_SECONDS`
- `MD_CONVERTER_WORKER_MEMORY_BUDGET_BYTES`
- `MD_CONVERTER_WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES`
- `MD_CONVERTER_WORKER_LEASE_SECONDS`
- `MD_CONVERTER_WORKER_HEARTBEAT_SECONDS`
- `MD_CONVERTER_WORKER_INCOMPLETE_SUBMISSION_SECONDS`
- `MD_CONVERTER_WORKER_IDLE_POLL_SECONDS`
- `MD_CONVERTER_WORKER_ERROR_BACKOFF_SECONDS`
- `MD_CONVERTER_WORKER_CLEANUP_INTERVAL_SECONDS`
- `MD_CONVERTER_WORKER_CLEANUP_BATCH_SIZE`
- `MD_CONVERTER_TEMPLATE_VERSION_RETENTION_SECONDS=31536000`
- `MD_CONVERTER_TEMPLATE_MIN_RETAINED_VERSIONS=10`
- `MD_CONVERTER_AUDIT_RETENTION_SECONDS=31536000`
- `MD_CONVERTER_CLAMAV_HOST` (defaults to loopback; set the deployment service name explicitly)
- `MD_CONVERTER_CLAMAV_PORT` (standard clamd TCP port `3310` unless deployment requires another)
- `MD_CONVERTER_CLAMAV_TIMEOUT_SECONDS` (defaults to 5 seconds)

The heartbeat must be shorter than the lease. All durations must be finite and positive. No ordering
is imposed between upload and decompressed-content ceilings.

## Recovery targets and quarterly proof

Standalone deployments must meet RPO 24 hours and RTO 4 hours. Distributed deployments must meet
RPO 1 hour and RTO 2 hours. At least once per calendar quarter, schedule
`uv run python scripts/run_restore_exercise.py` against an isolated restore target. The supplied
command must restore the named backup, validate representative stable object identifiers, and
finish only after readiness succeeds. The runner enforces the profile RTO as its subprocess timeout,
measures backup age and recovery with a monotonic elapsed-time clock while retaining UTC start and
completion timestamps, suppresses command output, and retains an immutable owner-only JSON report.
Store that report directory in protected durable operational storage; it is not subject to
application cleanup.
