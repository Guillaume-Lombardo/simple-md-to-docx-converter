# Storage profiles and recovery

The application requires one explicit `MARKWEAVE_STORAGE_PROFILE`. Configuration validation
rejects mixed or incomplete profile settings before adapters are constructed. Alembic upgrades run
when the application components are assembled; the standalone profile has one replica, while the
distributed migration runner uses the same schema history for PostgreSQL.

Both profiles require explicit template activation policy; there are no production defaults:

```text
MARKWEAVE_TEMPLATE_MAX_ARCHIVE_BYTES=<approved value>
MARKWEAVE_TEMPLATE_REQUEST_MAX_BYTES=<approved value greater than archive limit>
MARKWEAVE_TEMPLATE_METADATA_REQUEST_MAX_BYTES=<approved value>
MARKWEAVE_TEMPLATE_MAX_NAME_CHARACTERS=<approved value>
MARKWEAVE_TEMPLATE_MAX_DESCRIPTION_CHARACTERS=<approved value>
MARKWEAVE_TEMPLATE_MAX_ENTRIES=<approved value>
MARKWEAVE_TEMPLATE_MAX_MEMBER_BYTES=<approved value>
MARKWEAVE_TEMPLATE_MAX_TOTAL_BYTES=<approved value>
MARKWEAVE_TEMPLATE_MAX_COMPRESSION_RATIO=<approved value>
MARKWEAVE_TEMPLATE_MAX_XML_ELEMENTS=<approved value>
MARKWEAVE_TEMPLATE_MAX_XML_DEPTH=<approved value>
MARKWEAVE_TEMPLATE_MAX_XML_ATTRIBUTES=<approved value>
MARKWEAVE_TEMPLATE_MAX_DECLARED_FONTS=<approved value>
MARKWEAVE_TEMPLATE_MAX_FONT_NAME_CHARACTERS=<approved value>
MARKWEAVE_TEMPLATE_PANDOC_EXECUTABLE=<approved executable path>
MARKWEAVE_TEMPLATE_LIBREOFFICE_EXECUTABLE=<approved executable path>
MARKWEAVE_TEMPLATE_ENGINE_TIMEOUT_SECONDS=<approved value>
MARKWEAVE_TEMPLATE_ENGINE_TERMINATION_GRACE_SECONDS=<approved value>
MARKWEAVE_TEMPLATE_PENDING_PUBLICATION_STALE_SECONDS=<approved value>
MARKWEAVE_TEMPLATE_VERSION_RETENTION_SECONDS=31536000
MARKWEAVE_TEMPLATE_MIN_RETAINED_VERSIONS=10
MARKWEAVE_AUDIT_RETENTION_SECONDS=31536000
MARKWEAVE_CLAMAV_HOST=<clamd service name>
MARKWEAVE_CLAMAV_PORT=3310
MARKWEAVE_CLAMAV_TIMEOUT_SECONDS=5
MARKWEAVE_READINESS_TIMEOUT_SECONDS=<approved positive finite value>
MARKWEAVE_WORKER_METRICS_BIND_HOST=<external-worker metrics bind host>
MARKWEAVE_WORKER_METRICS_PORT=<external-worker metrics bind port>
MARKWEAVE_WORKER_METRICS_MAX_CONNECTIONS=<bounded positive value>
MARKWEAVE_WORKER_METRICS_OBSERVATION_LIMIT=<positive value not exceeding max connections>
MARKWEAVE_WORKER_METRICS_ACCEPT_QUEUE_SIZE=<bounded positive value>
MARKWEAVE_WORKER_METRICS_REQUEST_TIMEOUT_SECONDS=<approved positive finite value>
MARKWEAVE_TEMPLATE_ENGINE_WORKSPACE_ROOT=<optional bounded workspace parent>
```

Template activation invokes both configured document engines synchronously inside a bounded worker
thread, so request handlers do not block the ASGI event loop. Startup also retries durable hidden
publication reservations and deletion tombstones before accepting traffic.

Both profiles also require the complete resource policy below. Placeholders intentionally do
not establish production values:

```text
MARKWEAVE_CONVERSION_UPLOAD_MAX_BYTES=<approved positive value>
MARKWEAVE_CONVERSION_REQUEST_MAX_BYTES=<approved value greater than upload limit>
MARKWEAVE_CONVERSION_MAX_DECOMPRESSED_BYTES=<approved positive value>
MARKWEAVE_CONVERSION_MAX_FILES=<approved positive value>
MARKWEAVE_CONVERSION_MAX_IMAGES=<approved positive value>
MARKWEAVE_CONVERSION_MAX_DIAGRAMS=<approved positive value>
MARKWEAVE_CONVERSION_RETRY_AFTER_SECONDS=<approved positive value>
MARKWEAVE_JOB_RESULT_RETENTION_SECONDS=<approved positive value>
MARKWEAVE_JOB_ACTIVE_LIMIT_PER_USER=<approved positive value>
MARKWEAVE_JOB_GLOBAL_QUEUE_CAPACITY=<approved positive value>
MARKWEAVE_JOB_MAX_DURATION_SECONDS=<approved positive finite value>
MARKWEAVE_WORKER_MEMORY_BUDGET_BYTES=<approved positive value>
MARKWEAVE_WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES=<approved positive value>
MARKWEAVE_WORKER_LEASE_SECONDS=<approved positive finite value>
MARKWEAVE_WORKER_HEARTBEAT_SECONDS=<approved finite value shorter than lease>
MARKWEAVE_WORKER_INCOMPLETE_SUBMISSION_SECONDS=<approved positive finite value>
MARKWEAVE_WORKER_IDLE_POLL_SECONDS=<approved positive finite value>
MARKWEAVE_WORKER_ERROR_BACKOFF_SECONDS=<approved positive finite value>
MARKWEAVE_WORKER_CLEANUP_INTERVAL_SECONDS=<approved positive finite value>
MARKWEAVE_WORKER_CLEANUP_BATCH_SIZE=<approved positive value>
```

Upload and decompressed-content limits are independent. A standalone Markdown upload can make the
upload ceiling larger than the decompressed archive ceiling, while another approved policy may do
the reverse. Configuration therefore validates each as positive without imposing an unsupported
ordering.

## Standalone

Set:

```text
MARKWEAVE_STORAGE_PROFILE=standalone
MARKWEAVE_STANDALONE_DATA_DIRECTORY=/data
# plus every shared resource-policy variable listed above
```

Metadata is stored in `/data/metadata.sqlite3`. Object bytes are stored below `/data/objects`;
every path component after its fixed namespace is a UUID. Writes use a temporary file in the
destination directory, synchronize its content, replace the destination, and synchronize the
directory. One application replica must have exclusive ownership of this PVC; never mount the
SQLite database from multiple pods.

Template identities, immutable owners, search fields, preferences, the system fallback, immutable
version metadata, and audit records are in the same database. Template bytes use the
`template-versions/<owner UUID>/<version UUID>` object namespace; visible names and uploaded
filenames never influence a key.
Conversion inputs and results use the `uploads` and `results` namespaces. Durable queue state,
leases, heartbeats, cancellation flags, attempts, safe failures, and expiration metadata remain in
the same SQLite database and therefore belong to the same coordinated recovery set.

For a consistent backup, stop application and worker writes or use SQLite's online backup API,
then copy both the database and the complete objects directory as one recovery set. Preserve file
ownership and modes. To restore, keep the application stopped, restore both parts to an empty data
directory, verify ownership for the arbitrary runtime UID, start the application, allow Alembic to
upgrade older metadata, and require `/health/ready` to succeed before admitting traffic.

## Distributed

Set:

```text
MARKWEAVE_STORAGE_PROFILE=distributed
MARKWEAVE_DISTRIBUTED_DATABASE_URL=postgresql+psycopg://...
MARKWEAVE_S3_BUCKET=...
# plus every shared resource-policy variable listed above
```

`MARKWEAVE_S3_ENDPOINT_URL` and `MARKWEAVE_S3_REGION` select an AWS S3-compatible endpoint.
Static credentials are optional for workloads using an AWS-compatible credential provider; when
used, `MARKWEAVE_S3_ACCESS_KEY_ID` and `MARKWEAVE_S3_SECRET_ACCESS_KEY` must be supplied
together through secrets. RustFS is the CI and k3s implementation, but application code uses only
AWS S3-compatible operations and contains no RustFS-specific API.

Back up PostgreSQL with the platform's supported physical backup or `pg_dump` procedure and protect
the S3-compatible bucket with the provider's backup/versioning facilities. A recovery point must
pair database state with object versions from the same coordinated window. Restore into isolated
database and bucket targets first, run the application migration against the restored database,
verify representative stable object identifiers, and require readiness before switching traffic.
Do not rewrite object keys from usernames, filenames, or template names during backup or restore.
Template identities, versions, audit, preferences, and fallback selection are part of the
PostgreSQL recovery set. Immutable template bytes use the same stable key layout in S3 as on the
standalone filesystem.
Conversion queue rows and their referenced upload/result objects are also one recovery unit.
External workers must be stopped or drained during a coordinated backup unless the database and
bucket platforms provide a consistent cross-service recovery point.

## Recovery objectives and exercises

Standalone recovery must meet RPO 24 hours and RTO 4 hours; distributed recovery must meet RPO 1
hour and RTO 2 hours. Run an automated isolated restore for each deployed profile at least once per
calendar quarter with `scripts/run_restore_exercise.py`. The restore command owns backup restoration,
stable-object verification, and readiness verification. The runner measures RTO with an elapsed
monotonic clock, records UTC timestamps, and writes an immutable, owner-only report containing no
document content or credentials. Retain
reports in protected durable operational storage outside application cleanup.

Both boundaries are contract-tested and exercised in the hardened rootless image. Production backup
consistency, restore sequencing, and evidence requirements are detailed in
[recovery.md](recovery.md).
