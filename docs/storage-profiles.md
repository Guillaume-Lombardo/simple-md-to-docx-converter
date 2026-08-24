# Storage profiles and recovery

The application requires one explicit `MD_CONVERTER_STORAGE_PROFILE`. Configuration validation
rejects mixed or incomplete profile settings before adapters are constructed. Alembic upgrades run
when the application components are assembled; the standalone profile has one replica, while the
distributed migration runner uses the same schema history for PostgreSQL.

## Standalone

Set:

```text
MD_CONVERTER_STORAGE_PROFILE=standalone
MD_CONVERTER_STANDALONE_DATA_DIRECTORY=/data
MD_CONVERTER_CONVERSION_UPLOAD_MAX_BYTES=<approved value>
MD_CONVERTER_CONVERSION_REQUEST_MAX_BYTES=<approved value greater than upload limit>
MD_CONVERTER_CONVERSION_RETRY_AFTER_SECONDS=<approved value>
MD_CONVERTER_JOB_RESULT_RETENTION_SECONDS=<approved value>
MD_CONVERTER_TEMPLATE_MAX_ARCHIVE_BYTES=<approved value>
MD_CONVERTER_TEMPLATE_REQUEST_MAX_BYTES=<approved value greater than archive limit>
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
MD_CONVERTER_STORAGE_PROFILE=distributed
MD_CONVERTER_DISTRIBUTED_DATABASE_URL=postgresql+psycopg://...
MD_CONVERTER_S3_BUCKET=...
MD_CONVERTER_CONVERSION_UPLOAD_MAX_BYTES=<approved value>
MD_CONVERTER_CONVERSION_REQUEST_MAX_BYTES=<approved value greater than upload limit>
MD_CONVERTER_CONVERSION_RETRY_AFTER_SECONDS=<approved value>
MD_CONVERTER_JOB_RESULT_RETENTION_SECONDS=<approved value>
MD_CONVERTER_TEMPLATE_MAX_ARCHIVE_BYTES=<approved value>
MD_CONVERTER_TEMPLATE_REQUEST_MAX_BYTES=<approved value greater than archive limit>
```

`MD_CONVERTER_S3_ENDPOINT_URL` and `MD_CONVERTER_S3_REGION` select an AWS S3-compatible endpoint.
Static credentials are optional for workloads using an AWS-compatible credential provider; when
used, `MD_CONVERTER_S3_ACCESS_KEY_ID` and `MD_CONVERTER_S3_SECRET_ACCESS_KEY` must be supplied
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

## Operational decisions still open

No production retention, cleanup schedule, quota, antivirus policy, RPO, or RTO is fixed here.
Request-body size, upload size, polling advice, and result retention must be supplied explicitly;
their approved production values remain T18 work. Operators must keep those values configurable
until they receive separate product approval. T12 verifies real
SQLite/filesystem and PostgreSQL/RustFS boundaries. Final hardened-image rootless storage E2E is
the explicitly approved T20/T21 sequencing debt and is not an integration-test waiver.
