# Conversion jobs and workers

## HTTP contract

Authenticated clients submit multipart Markdown sources to `POST /api/v1/conversions` with a
template identity, immutable template-version identity, and `docx`, `pdf`, or `both` output. The
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

The required `MD_CONVERTER_CONVERSION_UPLOAD_MAX_BYTES`,
`MD_CONVERTER_CONVERSION_REQUEST_MAX_BYTES`,
`MD_CONVERTER_CONVERSION_RETRY_AFTER_SECONDS`, and
`MD_CONVERTER_JOB_RESULT_RETENTION_SECONDS` settings deliberately have no production defaults.
T18 owns their approved values and the associated quotas and schedules.

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
concurrency, and stop values are caller-owned and remain production policy for T18.

The processor port is intentionally storage-neutral. `FrozenTemplateJobProcessor` resolves the
exact frozen template pair through `TemplateService.resolve_frozen_version`, verifies the stored
size and SHA-256, and passes the immutable version metadata and bytes to the document processor;
later replacement or restoration cannot change a queued job's reference bytes. T20 owns final-image
process-mode wiring. Final rootless-image E2E
coverage therefore remains sequenced to T20/T21; T13 covers the
HTTP workflow against assembled ASGI storage and real worker paths through SQLite/filesystem and
PostgreSQL/S3-compatible storage.

## Browser workflow

The authenticated `/convert` page consumes this API without weakening its ownership, CSRF,
idempotency, or frozen-template rules. It submits a unique key with the multipart request, uses the
returned `Retry-After` value before progressively backing off status requests, and offers cancel or
download actions only for appropriate states. Recent owner-scoped jobs can be reopened. See
`docs/conversion-ui.md` for the user-facing behavior and its T20/T21 final-image E2E sequencing.
