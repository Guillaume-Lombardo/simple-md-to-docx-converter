# Observability, audit, and readiness

## Correlation and JSON logs

Every HTTP response carries `X-Correlation-ID`. A caller-supplied value is retained only when it is
one to 128 ASCII letters, digits, dots, underscores, or hyphens and starts with a letter or digit.
Invalid input is replaced with an application-generated identifier and is never reflected. Job
submission stores the accepted identifier with the durable queue row, so an external or embedded
worker restores the same correlation context after a restart or cross-process claim.

Application events use one-line JSON. The fixed schema includes timestamp, level, event,
correlation identifier, and selected stable identifiers or low-cardinality state fields. The
logger does not accept document content, uploaded filenames, credentials, arbitrary request data,
exception text, storage keys, SQL parameters, or local paths. Request-completion logs include only
method, status, duration, and correlation. Job lifecycle logs may include job, owner, immutable
template-version, and worker identifiers so an authorized operator can follow durable execution.

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

## Audit and version traceability

`GET /api/v1/audit?offset=0&limit=50` is restricted to administrators and returns a bounded newest-
first view of the existing immutable, content-free audit records. Each record includes actor,
owner, operation, stable target identifier, optional immutable template-version identifier,
administrator-intervention flag, and UTC timestamp. It contains no template names, descriptions,
filenames, document bytes, or credentials. The existing 365-day audit retention and immutable
cleanup-evidence policy remains unchanged.

Conversion status continues to expose its immutable `template_version_id` and the sorted converter,
Pandoc, Mermaid CLI, Chromium, and LibreOffice versions. It now also exposes the durable correlation
identifier used by the worker. These values identify the execution inputs without exposing stored
content or storage locations.

## Readiness

`GET /health/ready` never runs a conversion, reads an upload, or invokes a document engine. The
standalone profile performs one `SELECT 1` and one local object-root access check. The distributed
profile performs one `SELECT 1` and one S3-compatible `HeadBucket`. Database connect/statement and
S3 connect/read operations use the required positive finite
`MD_CONVERTER_READINESS_TIMEOUT_SECONDS`; S3 readiness disables retries so one probe remains one
bounded provider operation. Any component failure returns the stable content-free `NOT_READY`
response. Liveness remains independent at `GET /health/live`.
