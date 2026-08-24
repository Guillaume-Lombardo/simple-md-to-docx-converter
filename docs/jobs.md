# Conversion jobs and workers

## HTTP contract

Authenticated clients submit multipart Markdown sources to `POST /api/v1/conversions` with a
template identity, immutable template-version identity, and `docx`, `pdf`, or `both` output. The
source filename must end in `.md` or `.zip`; Markdown is decoded as strict UTF-8, while ZIP inputs
are validated by the secure archive adapter before conversion. The validated private leaf filename,
explicit source kind, byte size, and SHA-256 are persisted with the owner-bound object identifier.
Workers never infer the kind from magic bytes; they reject missing legacy metadata or any
filename/kind/content/size/digest mismatch with the safe `source_integrity` failure. Historical
terminal jobs created before migration `20260825_12` remain readable, while an older non-terminal
job without source metadata fails closed if claimed. The
job transaction locks the template identity and accepts only the exact active, published current
pair, preventing an archive or replacement race from changing the frozen selection. The server
then reserves a durable job row, atomically stores its source, and activates the queue row
before returning `202 Accepted`, `Location`, and `Retry-After`. Abandoned source reservations are
failed and cleaned by recovery, so a process crash cannot create an untracked private object.
`Idempotency-Key` is hashed and scoped to the authenticated owner; replaying the same
request returns the original job, while changing its content or parameters returns a stable 409.

Owners list their jobs and read status under `/api/v1/conversions`. Owners and administrators may
read an individual job, request cancellation, and download a successful result. Unauthorized job
identifiers are indistinguishable from absent ones. Responses expose only stable state, progress,
attempt, template identifiers, and safe functional failures—never paths, SQL, leases, or worker
identifiers. Status responses also include result expiration, the immutable template version, and
the locked converter, Pandoc, Mermaid CLI, Chromium, and LibreOffice versions needed for
traceability.
PDF and combined results also expose their canonical external traceability JSON at
`GET /api/v1/conversions/{job_id}/result/manifest`. The worker writes the result and deterministic
owner-bound manifest sidecar before publishing both identifiers in the same fenced database
transition. Publication failure or cancellation compensates both objects; expiration removes every
attempt from both namespaces. DOCX-only jobs have no manifest result.
Status also returns the durable correlation identifier accepted at submission. An embedded or
external worker restores the same identifier when it claims the job; the content-free logging and
metric contract is documented in [`observability.md`](observability.md).

The required `MD_CONVERTER_CONVERSION_UPLOAD_MAX_BYTES`,
`MD_CONVERTER_CONVERSION_REQUEST_MAX_BYTES`,
`MD_CONVERTER_CONVERSION_RETRY_AFTER_SECONDS`, and
`MD_CONVERTER_JOB_RESULT_RETENTION_SECONDS` settings deliberately have no production defaults.
T18 supplies their validated configurable contracts together with the associated quotas and
schedules; operators must provide environment-specific values.
The complete typed admission, budget, retention, and cleanup contract is documented in
[`resource-policy.md`](resource-policy.md).

## Durable lifecycle

Jobs move through `queued`, `running`, and exactly one of `succeeded`, `failed`, or `cancelled`.
Bounded cleanup changes retained terminal rows to `expired` under a durable cleanup claim. Safe
steps and progress are updated only by the current lease owner. Every claim receives an unguessable
fencing token, and every attempt derives a distinct result-object UUID from the job UUID and attempt
number. A stale worker can therefore remove only its own unpublished attempt. Terminal cleanup
enumerates every attempt key and uses an expiring cleanup token, making compensation retry-safe
after a crash or competing cleanup worker.

Claims are oldest-first. PostgreSQL locks candidates with `FOR UPDATE SKIP LOCKED`; the standalone
profile relies on its mandatory single-replica SQLite topology and a conditional queued-state
update. A dedicated periodic heartbeat extends leases independently of processor progress and only
before expiry. Recovery requeues expired running jobs, or
finishes them as cancelled when cancellation was already durable. Attempts remain monotonic across
recovery. Terminal transitions atomically honor a durable cancellation request before success or
failure can be published.

## Worker modes and sequencing

`WorkerLoop` is shared by external processes and the standalone `EmbeddedWorker` lifecycle. It
recovers expired leases and abandoned uploads continuously, processes one claim at a time, retries
sanitized transient repository and object-store failures with bounded backoff, waits without busy
looping, and performs bounded periodic cleanup. The embedded lifecycle exposes unexpected terminal
failures to its readiness owner. Polling, lease, heartbeat, recovery, cleanup, retention,
concurrency, and stop values are caller-owned and have no implicit production defaults. Cleanup is
scheduled from elapsed monotonic time rather than loop iterations, so queue activity cannot make it
run too frequently or prevent an idle worker from running it.

Distributed entrypoints use `AppComponents.build_external_worker_runtime`. That lifecycle wraps
the shared loop with the independently bound process-local metrics listener documented in
`docs/observability.md`; calling the bare external loop does not satisfy the runtime contract.

The processor port is intentionally storage-neutral. `FrozenTemplateJobProcessor` resolves the
exact frozen template pair through `TemplateService.resolve_frozen_version`, verifies the stored
size and SHA-256, and passes the immutable version metadata and bytes to the document processor;
later replacement or restoration cannot change a queued job's reference bytes. It also passes the
worker's absolute monotonic deadline so document processors can cap every engine invocation by the
remaining overall duration. T20 provides the production template-aware processor and final-image
process-mode wiring. Its container smoke tests cover both standalone and distributed modes; T21
retains the complete user-workflow E2E matrix. T13 covers the
HTTP workflow against assembled ASGI storage and real worker paths through SQLite/filesystem and
PostgreSQL/S3-compatible storage.

## Browser workflow

The authenticated `/convert` page consumes this API without weakening its ownership, CSRF,
idempotency, or frozen-template rules. It submits a unique key with the multipart request, uses the
returned `Retry-After` value before progressively backing off status requests, and offers cancel or
download actions only for appropriate states. Recent owner-scoped jobs can be reopened. See
`docs/conversion-ui.md` for the user-facing behavior and its T20/T21 final-image E2E sequencing.
