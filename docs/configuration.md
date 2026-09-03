# Configuration reference

The `0.6.0` deployment has separate backend, frontend, and public-router processes. Frontend and
router origins, host allowlists, TLS key/certificate paths, request limits, and the positive bounded
upstream inactivity timeout (`ROUTER_UPSTREAM_TIMEOUT_MS`) belong to
their process configuration; they do not grant the frontend database, object-store, scanner, or
authentication credentials. Loopback quickstarts remain HTTP and require a matched immutable image
pair when testing an unpublished candidate.

The application reads case-insensitive `MARKWEAVE_*` environment variables and fails startup with a
content-free error when the assembled settings are invalid. "Required" below means there is
deliberately no application default; operators must choose an approved value. Defaults are
implementation defaults, not approval to use them unchanged in production.

## 0.x configuration migration

`MARKWEAVE_*` is the only prefix used in new deployments, Compose files, quickstarts, examples,
and diagnostics. `MD_CONVERTER_*` remains a deprecated compatibility alias throughout every 0.x
release and will be removed in 1.0.

For a staged migration, rename each variable from `MD_CONVERTER_<SUFFIX>` to
`MARKWEAVE_<SUFFIX>`. Defining both forms is allowed only when they mean the same validated value:
for example, `MARKWEAVE_PORT=8080` and `MD_CONVERTER_PORT=08080` are equivalent. Passwords,
database URLs, S3 credentials, and other secret or opaque values must match exactly as supplied.
Any incompatible pair, including an invalid alias spelling, prevents startup without displaying
either value. This fail-closed behavior avoids silently selecting a configuration source.

The public Compose quickstart uses a T39-capable image and supplies only `MARKWEAVE_*` variables.
The deprecated aliases remain available only for operators migrating an older external deployment;
do not define both prefixes in new deployments.

The default cookie names remain `md_converter_session` and `__Host-md_converter_csrf` throughout
0.x. Do not rename them during this environment-prefix migration; a deliberate session/cookie
migration belongs to the 1.0 boundary. The image remains
`ghcr.io/guillaume-lombardo/md-converter` throughout 0.x. Renaming it requires a separate
dual-publication migration.

The container/runtime bind settings are:

| Environment variable | Runtime default | Applies to / handling |
| --- | --- | --- |
| `MARKWEAVE_HOST` | `0.0.0.0` in the image | API and embedded-worker HTTP bind address; restrict exposure in the platform |
| `MARKWEAVE_PORT` | `8080` in the image | API and embedded-worker HTTP listen port; valid Uvicorn integer port required |

They are validated application settings. Service publication and accepted hostnames remain
deployment concerns; binding a socket does not authorize public exposure.

## Identity and HTTP security

| Environment variable | Requirement or default | Applies to / handling |
| --- | --- | --- |
| `MARKWEAVE_INITIAL_ADMIN_USERNAME` | Required, nonblank | Both profiles; secret-adjacent bootstrap input |
| `MARKWEAVE_INITIAL_ADMIN_PASSWORD` | Required, nonblank | Both profiles; Secret, rotate after bootstrap |
| `MARKWEAVE_USER_PROVISIONING_FILE` | Optional path | Both profiles; strict UTF-8 startup CSV mounted as a secret |
| `MARKWEAVE_ARGON2_MEMORY_COST` | `19456`, minimum 8 | Both profiles |
| `MARKWEAVE_ARGON2_TIME_COST` | `2`, minimum 1 | Both profiles |
| `MARKWEAVE_ARGON2_PARALLELISM` | `1`, minimum 1 | Both profiles |
| `MARKWEAVE_SESSION_TOKEN_BYTES` | `32`, minimum 16 | Both profiles |
| `MARKWEAVE_SESSION_IDLE_SECONDS` | Deprecated 0.x compatibility input | Accepted with a startup warning; persisted administrator policy is authoritative |
| `MARKWEAVE_SESSION_ABSOLUTE_SECONDS` | `28800`, positive | Both profiles; operator hard ceiling for every role policy |
| `MARKWEAVE_SESSION_COOKIE_NAME` | `md_converter_session`, nonblank | Both profiles |
| `MARKWEAVE_PUBLIC_ORIGIN` | Optional | Both profiles; exact HTTP(S) scheme, host, optional port only |
| `MARKWEAVE_INSECURE_EVALUATION_MODE` | `false` | Both profiles; explicit loopback-only test exception |

`MARKWEAVE_PUBLIC_ORIGIN` is authoritative for Origin checks behind a TLS-terminating proxy.
Paths, queries, fragments, and user information are rejected. Forwarded headers remain untrusted.
When it is unset, the direct ASGI request base URL is authoritative.
Setting `MARKWEAVE_INSECURE_EVALUATION_MODE=true` disables both this protection and upload
malware scanning. That exception exists only for the loopback-bound
`quickstart-simple.sh up --insecure` SSH-tunnel workflow. It must remain `false` in production and
in every network-accessible deployment.

`MARKWEAVE_USER_PROVISIONING_FILE` is applied after migration and initial-administrator
bootstrap but before requests are served. See [local authentication](authentication.md) for the
exact CSV contract, replacement behavior, concurrent-start serialization, and plaintext-secret
handling requirements.

Role-specific idle durations are application data, not deployment configuration. With no persisted
override they are 30 minutes for standard users and 15 minutes for administrators. Administrators
replace the pair through the versioned FastAPI policy endpoint within its documented bounds. The
absolute lifetime can be shorter than the stored default, but an administrator update is rejected
if either proposed duration exceeds it. Existing sessions remain capped by the absolute lifetime. Preserve the policy
row and its immutable audits in database backup, restore, and rollback procedures.

## Conversion and engine limits

| Environment variable | Requirement | Applies to / constraint |
| --- | --- | --- |
| `MARKWEAVE_CONVERSION_UPLOAD_MAX_BYTES` | Required positive integer | Both profiles |
| `MARKWEAVE_CONVERSION_REQUEST_MAX_BYTES` | Required positive integer | Both; must exceed upload limit |
| `MARKWEAVE_CONVERSION_MAX_DECOMPRESSED_BYTES` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_MAX_FILES` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_MAX_IMAGES` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_MAX_DIAGRAMS` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_MAX_COMPRESSION_RATIO` | Required finite number, at least 1 | Both |
| `MARKWEAVE_CONVERSION_IMAGE_MAX_SOURCE_BYTES` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_IMAGE_MAX_WIDTH_PIXELS` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_IMAGE_MAX_HEIGHT_PIXELS` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_IMAGE_MAX_PIXELS` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_IMAGE_MAX_SVG_ELEMENTS` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_IMAGE_MAX_SVG_DEPTH` | Required integer from 1 to 64 | Both |
| `MARKWEAVE_CONVERSION_MERMAID_MAX_SOURCE_BYTES` | Required positive integer | Both; per diagram |
| `MARKWEAVE_CONVERSION_MERMAID_MAX_TOTAL_SOURCE_BYTES` | Required positive integer | Both; at least per-diagram source limit |
| `MARKWEAVE_CONVERSION_MERMAID_MAX_OUTPUT_BYTES` | Required positive integer | Both; per diagram |
| `MARKWEAVE_CONVERSION_MERMAID_MAX_TOTAL_OUTPUT_BYTES` | Required positive integer | Both; at least per-diagram output limit |
| `MARKWEAVE_CONVERSION_MERMAID_MAX_WIDTH_PIXELS` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_MERMAID_MAX_HEIGHT_PIXELS` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_MERMAID_EXECUTABLE` | Required nonblank path/name | Worker modes; locked local executable |
| `MARKWEAVE_CONVERSION_CHROMIUM_EXECUTABLE` | Required nonblank path/name | Worker modes; locked local executable |
| `MARKWEAVE_CONVERSION_PDF_CANCELLATION_POLL_SECONDS` | Required positive finite number | Worker modes |
| `MARKWEAVE_CONVERSION_PDF_MAX_BYTES` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_PDF_MAX_DECODED_STREAM_BYTES` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_PDF_MAX_PAGES` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_PDF_MAX_OBJECTS` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_PDF_MAX_OBJECT_DEPTH` | Required positive integer | Both |
| `MARKWEAVE_CONVERSION_FONT_MANIFEST_PATH` | Required path | Both; image's locked font manifest |
| `MARKWEAVE_CONVERSION_RETRY_AFTER_SECONDS` | Required positive integer | API responses in both profiles |

## Jobs, workers, metrics, and retention

| Environment variable | Requirement or default | Applies to / constraint |
| --- | --- | --- |
| `MARKWEAVE_JOB_RESULT_RETENTION_SECONDS` | Required positive integer | Both profiles |
| `MARKWEAVE_JOB_ACTIVE_LIMIT_PER_USER` | Required positive integer | Both profiles |
| `MARKWEAVE_JOB_GLOBAL_QUEUE_CAPACITY` | Required positive integer | Both profiles |
| `MARKWEAVE_JOB_MAX_DURATION_SECONDS` | Required positive finite number | Worker modes |
| `MARKWEAVE_WORKER_MEMORY_BUDGET_BYTES` | Required positive integer | Worker modes and container limit |
| `MARKWEAVE_WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES` | Required positive integer | Worker modes and container limit |
| `MARKWEAVE_WORKER_LEASE_SECONDS` | Required positive finite number | Worker modes |
| `MARKWEAVE_WORKER_HEARTBEAT_SECONDS` | Required positive finite number | Worker modes; shorter than lease |
| `MARKWEAVE_WORKER_INCOMPLETE_SUBMISSION_SECONDS` | Required positive finite number | Worker modes |
| `MARKWEAVE_WORKER_IDLE_POLL_SECONDS` | Required positive finite number | Worker modes |
| `MARKWEAVE_WORKER_ERROR_BACKOFF_SECONDS` | Required positive finite number | Worker modes |
| `MARKWEAVE_WORKER_CLEANUP_INTERVAL_SECONDS` | Required positive finite number | Worker modes |
| `MARKWEAVE_WORKER_CLEANUP_BATCH_SIZE` | Required positive integer | Worker modes |
| `MARKWEAVE_WORKER_METRICS_BIND_HOST` | `127.0.0.1` | External worker; valid printable ASCII host |
| `MARKWEAVE_WORKER_METRICS_PORT` | `9464` | External worker; 1–65535 |
| `MARKWEAVE_WORKER_METRICS_MAX_CONNECTIONS` | `4` | External worker; 1–64 |
| `MARKWEAVE_WORKER_METRICS_OBSERVATION_LIMIT` | `2` | External worker; no greater than connections |
| `MARKWEAVE_WORKER_METRICS_ACCEPT_QUEUE_SIZE` | `8` | External worker; 1–128 |
| `MARKWEAVE_WORKER_METRICS_REQUEST_TIMEOUT_SECONDS` | `2.0` | External worker; positive finite number |

The API-only distributed process still validates the assembled shared settings at startup. Match
the memory and ephemeral-storage configuration exactly to the worker container limits.

## Templates, scanning, and readiness

| Environment variable | Requirement or default | Applies to / constraint |
| --- | --- | --- |
| `MARKWEAVE_TEMPLATE_MAX_ARCHIVE_BYTES` | Required positive integer | Both profiles |
| `MARKWEAVE_TEMPLATE_REQUEST_MAX_BYTES` | Required positive integer | Both; must exceed archive limit |
| `MARKWEAVE_TEMPLATE_METADATA_REQUEST_MAX_BYTES` | Required positive integer | Both |
| `MARKWEAVE_TEMPLATE_MAX_NAME_CHARACTERS` | Required positive integer | Both |
| `MARKWEAVE_TEMPLATE_MAX_DESCRIPTION_CHARACTERS` | Required positive integer | Both |
| `MARKWEAVE_TEMPLATE_MAX_ENTRIES` | Required positive integer | Both |
| `MARKWEAVE_TEMPLATE_MAX_MEMBER_BYTES` | Required positive integer | Both |
| `MARKWEAVE_TEMPLATE_MAX_TOTAL_BYTES` | Required positive integer | Both |
| `MARKWEAVE_TEMPLATE_MAX_COMPRESSION_RATIO` | Required number, at least 1 | Both |
| `MARKWEAVE_TEMPLATE_MAX_XML_ELEMENTS` | Required positive integer | Both |
| `MARKWEAVE_TEMPLATE_MAX_XML_DEPTH` | Required positive integer | Both |
| `MARKWEAVE_TEMPLATE_MAX_XML_ATTRIBUTES` | Required positive integer | Both |
| `MARKWEAVE_TEMPLATE_MAX_DECLARED_FONTS` | Required positive integer | Both |
| `MARKWEAVE_TEMPLATE_MAX_FONT_NAME_CHARACTERS` | Required positive integer | Both |
| `MARKWEAVE_TEMPLATE_PANDOC_EXECUTABLE` | Required nonblank path/name | Both; locked local executable |
| `MARKWEAVE_TEMPLATE_LIBREOFFICE_EXECUTABLE` | Required nonblank path/name | Both; locked local executable |
| `MARKWEAVE_TEMPLATE_ENGINE_TIMEOUT_SECONDS` | Required positive number | Both |
| `MARKWEAVE_TEMPLATE_ENGINE_TERMINATION_GRACE_SECONDS` | Required positive number | Both |
| `MARKWEAVE_TEMPLATE_PENDING_PUBLICATION_STALE_SECONDS` | Required positive finite number | Both |
| `MARKWEAVE_TEMPLATE_VERSION_RETENTION_SECONDS` | `31536000` | Both |
| `MARKWEAVE_TEMPLATE_MIN_RETAINED_VERSIONS` | `10`, minimum 10 | Both |
| `MARKWEAVE_AUDIT_RETENTION_SECONDS` | `31536000` | Both |
| `MARKWEAVE_READINESS_TIMEOUT_SECONDS` | Required positive finite number | API modes in both profiles |
| `MARKWEAVE_TEMPLATE_ENGINE_WORKSPACE_ROOT` | Optional path | Both; bounded workspace parent |
| `MARKWEAVE_MALWARE_SCANNING_MODE` | `clamav` | Both; `clamav` or `trusted-upstream` |
| `MARKWEAVE_CLAMAV_HOST` | `127.0.0.1` | Both; set service host explicitly in deployment |
| `MARKWEAVE_CLAMAV_PORT` | `3310` | Both; 1–65535 |
| `MARKWEAVE_CLAMAV_TIMEOUT_SECONDS` | `5.0` | Both; positive finite number |

In the default `clamav` mode, scanner unavailability fails closed. `trusted-upstream` performs no
local malware scan and must be selected only when a proxy scans every upload before forwarding it
and network policy prevents all direct or alternate application access. Startup emits a warning in
that mode because the operator, rather than Markweave, owns and asserts the scanning boundary.
The separate `MARKWEAVE_INSECURE_EVALUATION_MODE=true` exception also bypasses this scanner and
emits an insecure-mode warning; it is limited to the documented loopback SSH-tunnel quickstart.
Credentials or document content must never be placed in these settings.

## Storage profiles and secrets

| Environment variable | Requirement | Applies to / handling |
| --- | --- | --- |
| `MARKWEAVE_STORAGE_PROFILE` | Required: `standalone` or `distributed` | Selects one coherent profile |
| `MARKWEAVE_STANDALONE_DATA_DIRECTORY` | Required for standalone; forbidden for distributed | Persistent SQLite and object root |
| `MARKWEAVE_DISTRIBUTED_DATABASE_URL` | Required for distributed; forbidden for standalone | Secret PostgreSQL URL |
| `MARKWEAVE_S3_BUCKET` | Required for distributed; forbidden for standalone | Bucket name |
| `MARKWEAVE_S3_ENDPOINT_URL` | Optional distributed setting; forbidden for standalone | AWS S3-compatible endpoint |
| `MARKWEAVE_S3_REGION` | Optional distributed setting; forbidden for standalone | AWS region |
| `MARKWEAVE_S3_ACCESS_KEY_ID` | Optional only with secret key; forbidden for standalone | Secret static credential |
| `MARKWEAVE_S3_SECRET_ACCESS_KEY` | Optional only with access key; forbidden for standalone | Secret static credential |

Omitting both static S3 credentials allows an AWS-compatible workload credential provider. Inject
all secrets through the platform's secret mechanism; do not put them in images, ConfigMaps,
manifests, commands, logs, or documentation examples.

See [storage profiles](storage-profiles.md) for data layout and [resource policy](resource-policy.md)
for runtime interactions.
