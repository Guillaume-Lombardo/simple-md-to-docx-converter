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

The HTTP adapter must translate `JobUserQuotaExceededError` to `429` and
`JobQueueCapacityExceededError` to `503`, using the configured
`MD_CONVERTER_CONVERSION_RETRY_AFTER_SECONDS` header value. That small adapter assembly is deferred
until the concurrent T17 change releases `app.py`; the domain and persistence errors contain no
database or document details.

## Document and worker budgets

The required upload, decompressed-byte, file, image, and diagram ceilings are assembled as a
`DocumentResourceBudget`. Existing archive, image, Mermaid, Pandoc, and LibreOffice adapters retain
their own finer-grained validated limits. Final conversion composition must derive those adapter
limits from this policy rather than supply independent values.

`MD_CONVERTER_JOB_MAX_DURATION_SECONDS` is enforced by the worker cancellation callback and by the
existing subprocess deadlines. A cooperative processor that reaches the overall deadline ends with
the stable `resource_budget_exceeded` failure. A durable user cancellation wins over the duration
failure. Engine process groups remain responsible for terminating their descendants.

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
retryable.

Template-version and audit retention require their owning template/audit contracts and are not
silently inferred here. Antivirus has no approved engine or provider contract. Both remain explicit
product/assembly gaps instead of permissive no-op behavior.

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

The heartbeat must be shorter than the lease. The decompressed-byte ceiling cannot be smaller than
the upload ceiling. All durations must be finite and positive.
