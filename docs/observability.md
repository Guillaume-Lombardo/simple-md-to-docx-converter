# Observability, audit, and readiness

## Correlation and JSON logs

Every HTTP response carries a fresh server-generated UUID in `X-Correlation-ID`. Caller-supplied
header text never influences that value, even when it is a syntactically valid UUID or looks like a
safe opaque token. It is neither logged nor persisted and is never reflected. Job submission stores
only the generated identifier with the durable queue row, so an external or embedded worker restores
the same server correlation context after a restart or cross-process claim. Clients correlate a
request by reading the response header rather than choosing the identifier.

Application events use one-line JSON. The fixed schema includes timestamp, level, event,
correlation identifier, and selected stable identifiers or low-cardinality state fields. The
logger does not accept document content, uploaded filenames, credentials, arbitrary request data,
exception text, storage keys, SQL parameters, or local paths. Request-completion logs include only
method, status, duration, and correlation. Job lifecycle logs may include job, owner, immutable
template-version, and worker identifiers so an authorized operator can follow durable execution.
Event names are fixed. UUID fields must be canonical UUID strings; worker identifiers are limited
to 64 ASCII letters, digits, underscores, or hyphens; methods, states, steps, operations, and error
codes use fixed vocabularies; and numeric fields are finite and bounded. Unsafe direct logging
values are omitted and an unsafe event is replaced, while the application logging API rejects an
invalid value before it reaches a handler.

## Metrics

`GET /metrics` returns Prometheus text format without user, job, template, filename, path, or
correlation labels. Queue gauges come from one aggregate metadata query and do not materialize job
rows. The endpoint exposes:

- `md_converter_queue_depth` and `md_converter_queue_oldest_age_seconds`;
- `md_converter_active_jobs`;
- `md_converter_job_step_duration_seconds_count` and `_sum`, labelled only by the fixed step;
- `md_converter_job_failures_total`, labelled by stable safe error code;
- `md_converter_job_saturation_total`, labelled `owner` or `global`;
- `md_converter_job_expirations_total`, `md_converter_worker_retries_total`, and
  `md_converter_job_recoveries_total`;
- request totals and duration sums labelled only by HTTP method and status.

Counters are process-local and reset on process restart. Durable job state remains authoritative;
queue depth, age, and active-job gauges are recomputed from the selected SQLite or PostgreSQL
profile for every scrape. Distributed deployments aggregate process-local counters in the metrics
backend and must not sum the database-derived gauges across API replicas.

Each external-worker process must run the lifecycle returned by
`AppComponents.build_external_worker_runtime`, not the bare loop. It binds a process-local HTTP
listener at `MD_CONVERTER_WORKER_METRICS_BIND_HOST` and
`MD_CONVERTER_WORKER_METRICS_PORT`, serves only `GET /metrics`, and starts and stops with the worker
loop. `MD_CONVERTER_WORKER_METRICS_MAX_CONNECTIONS` fixes request concurrency,
`MD_CONVERTER_WORKER_METRICS_OBSERVATION_LIMIT` separately caps simultaneous queue queries,
`MD_CONVERTER_WORKER_METRICS_ACCEPT_QUEUE_SIZE` bounds the kernel accept queue, and
`MD_CONVERTER_WORKER_METRICS_REQUEST_TIMEOUT_SECONDS` applies one absolute request-line/header
deadline. Saturated connections receive a content-free `503` and close without entering an
unbounded executor queue. Bind and scrape failures are content-free and never expose provider details. API and worker
counters are intentionally separate process series; this surface does not claim in-process or
cross-replica aggregation. T20 must connect the external-worker command to this lifecycle.

## Audit and version traceability

`GET /api/v1/audit?offset=0&limit=50` is restricted to administrators and returns one bounded,
deterministically ordered newest-first view across immutable template and authentication audit
records. Each record includes actor, owner, operation, stable target identifier and type, target
version, optional immutable template-version identifier, administrator-intervention flag, and UTC
timestamp. Account creation, deactivation, reactivation, and password reset are committed in the
same database transaction as their content-free audit record; failed and unauthorized mutations
create no record. Bootstrap administrator creation is recorded once without being classified as
an administrator intervention. Audit data contains no usernames, template names, descriptions,
filenames, hashes, passwords, document bytes, or credentials. Both audit tables share the existing
365-day retention and one combined oldest-first cleanup query. A single global transaction lock
serializes cooperating PostgreSQL cleaners; the combined query applies one limit across both tables
and deletes only those selected rows, rather than locking or materializing one limit per table.
Direct updates and deletes are rejected. The cleanup transaction creates an uncommitted guard row,
deletes its globally selected candidates, removes the guard, and commits immutable content-free
cleanup evidence atomically. SQLite uses the same guard contract under its serialized write
transaction.

Conversion status continues to expose its immutable `template_version_id` and the sorted converter,
Pandoc, Mermaid CLI, Chromium, and LibreOffice versions. It now also exposes the durable correlation
identifier used by the worker. These values identify the execution inputs without exposing stored
content or storage locations.

## Readiness

`GET /health/ready` never runs a conversion, reads an upload, or invokes a document engine. The
standalone profile performs one `SELECT 1` and one local object-root access check. The distributed
profile performs one `SELECT 1` and one S3-compatible `HeadBucket`. Readiness owns separate database
and S3 clients: migrations and normal SQL/object operations retain their normal pool, timeout, and
retry behavior. Only the readiness database client disables pool pre-ping and applies a bounded
connect/statement budget; only the readiness S3 client disables retries and applies bounded
connect/read budgets. These probe-only clients use the required positive finite
`MD_CONVERTER_READINESS_TIMEOUT_SECONDS`; S3 readiness disables retries so one probe remains one
bounded provider operation. Any component failure returns the stable content-free `NOT_READY`
response. Liveness remains independent at `GET /health/live`.

## Final-image E2E sequencing

The source contract is complete in T19, but final-rootless-image validation is sequenced rather
than waived. T20 must run both standalone and distributed final images and verify API metrics,
isolated readiness success/failure, all account audit mutations plus authorization failures, and an
external worker whose independently bound metrics listener is concurrently scrapeable and stops
with the worker. It must also prove API and worker counters remain distinct. T21 must repeat both
profiles through the published deployment/restore path, scrape every API/worker process, and verify
authentication and template audit ordering/retention survives backup and restore. Results require
independent review before T19 is marked done.
