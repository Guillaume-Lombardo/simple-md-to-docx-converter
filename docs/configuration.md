# Configuration reference

The application reads case-insensitive environment variables with the `MD_CONVERTER_` prefix and
fails startup with a content-free error when the assembled settings are invalid. "Required" below
means there is deliberately no application default; operators must choose an approved value.
Defaults are implementation defaults, not approval to use them unchanged in production.

Two container/runtime variables are consumed before Pydantic settings assembly:

| Environment variable | Runtime default | Applies to / handling |
| --- | --- | --- |
| `MD_CONVERTER_HOST` | `0.0.0.0` in the image | API and embedded-worker HTTP bind address; restrict exposure in the platform |
| `MD_CONVERTER_PORT` | `8080` in the image | API and embedded-worker HTTP listen port; valid Uvicorn integer port required |

They are not `Settings` model fields. The container entrypoint and embedded-worker launcher pass
them directly to Uvicorn, so invalid values fail runtime startup rather than Pydantic configuration
validation. Service publication and accepted hostnames remain deployment concerns; binding a socket
does not authorize public exposure.

## Identity and HTTP security

| Environment variable | Requirement or default | Applies to / handling |
| --- | --- | --- |
| `MD_CONVERTER_INITIAL_ADMIN_USERNAME` | Required, nonblank | Both profiles; secret-adjacent bootstrap input |
| `MD_CONVERTER_INITIAL_ADMIN_PASSWORD` | Required, nonblank | Both profiles; Secret, rotate after bootstrap |
| `MD_CONVERTER_ARGON2_MEMORY_COST` | `19456`, minimum 8 | Both profiles |
| `MD_CONVERTER_ARGON2_TIME_COST` | `2`, minimum 1 | Both profiles |
| `MD_CONVERTER_ARGON2_PARALLELISM` | `1`, minimum 1 | Both profiles |
| `MD_CONVERTER_SESSION_TOKEN_BYTES` | `32`, minimum 16 | Both profiles |
| `MD_CONVERTER_SESSION_IDLE_SECONDS` | `1800`, positive | Both profiles |
| `MD_CONVERTER_SESSION_ABSOLUTE_SECONDS` | `28800`, positive | Both profiles; at least the idle lifetime |
| `MD_CONVERTER_SESSION_COOKIE_NAME` | `md_converter_session`, nonblank | Both profiles |
| `MD_CONVERTER_PUBLIC_ORIGIN` | Optional | Both profiles; exact HTTP(S) scheme, host, optional port only |

`MD_CONVERTER_PUBLIC_ORIGIN` is authoritative for Origin checks behind a TLS-terminating proxy.
Paths, queries, fragments, and user information are rejected. Forwarded headers remain untrusted.
When it is unset, the direct ASGI request base URL is authoritative.

## Conversion and engine limits

| Environment variable | Requirement | Applies to / constraint |
| --- | --- | --- |
| `MD_CONVERTER_CONVERSION_UPLOAD_MAX_BYTES` | Required positive integer | Both profiles |
| `MD_CONVERTER_CONVERSION_REQUEST_MAX_BYTES` | Required positive integer | Both; must exceed upload limit |
| `MD_CONVERTER_CONVERSION_MAX_DECOMPRESSED_BYTES` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_MAX_FILES` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_MAX_IMAGES` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_MAX_DIAGRAMS` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_MAX_COMPRESSION_RATIO` | Required finite number, at least 1 | Both |
| `MD_CONVERTER_CONVERSION_IMAGE_MAX_SOURCE_BYTES` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_IMAGE_MAX_WIDTH_PIXELS` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_IMAGE_MAX_HEIGHT_PIXELS` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_IMAGE_MAX_PIXELS` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_IMAGE_MAX_SVG_ELEMENTS` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_IMAGE_MAX_SVG_DEPTH` | Required integer from 1 to 64 | Both |
| `MD_CONVERTER_CONVERSION_MERMAID_MAX_SOURCE_BYTES` | Required positive integer | Both; per diagram |
| `MD_CONVERTER_CONVERSION_MERMAID_MAX_TOTAL_SOURCE_BYTES` | Required positive integer | Both; at least per-diagram source limit |
| `MD_CONVERTER_CONVERSION_MERMAID_MAX_OUTPUT_BYTES` | Required positive integer | Both; per diagram |
| `MD_CONVERTER_CONVERSION_MERMAID_MAX_TOTAL_OUTPUT_BYTES` | Required positive integer | Both; at least per-diagram output limit |
| `MD_CONVERTER_CONVERSION_MERMAID_MAX_WIDTH_PIXELS` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_MERMAID_MAX_HEIGHT_PIXELS` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_MERMAID_EXECUTABLE` | Required nonblank path/name | Worker modes; locked local executable |
| `MD_CONVERTER_CONVERSION_CHROMIUM_EXECUTABLE` | Required nonblank path/name | Worker modes; locked local executable |
| `MD_CONVERTER_CONVERSION_PDF_CANCELLATION_POLL_SECONDS` | Required positive finite number | Worker modes |
| `MD_CONVERTER_CONVERSION_PDF_MAX_BYTES` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_PDF_MAX_DECODED_STREAM_BYTES` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_PDF_MAX_PAGES` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_PDF_MAX_OBJECTS` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_PDF_MAX_OBJECT_DEPTH` | Required positive integer | Both |
| `MD_CONVERTER_CONVERSION_FONT_MANIFEST_PATH` | Required path | Both; image's locked font manifest |
| `MD_CONVERTER_CONVERSION_RETRY_AFTER_SECONDS` | Required positive integer | API responses in both profiles |

## Jobs, workers, metrics, and retention

| Environment variable | Requirement or default | Applies to / constraint |
| --- | --- | --- |
| `MD_CONVERTER_JOB_RESULT_RETENTION_SECONDS` | Required positive integer | Both profiles |
| `MD_CONVERTER_JOB_ACTIVE_LIMIT_PER_USER` | Required positive integer | Both profiles |
| `MD_CONVERTER_JOB_GLOBAL_QUEUE_CAPACITY` | Required positive integer | Both profiles |
| `MD_CONVERTER_JOB_MAX_DURATION_SECONDS` | Required positive finite number | Worker modes |
| `MD_CONVERTER_WORKER_MEMORY_BUDGET_BYTES` | Required positive integer | Worker modes and container limit |
| `MD_CONVERTER_WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES` | Required positive integer | Worker modes and container limit |
| `MD_CONVERTER_WORKER_LEASE_SECONDS` | Required positive finite number | Worker modes |
| `MD_CONVERTER_WORKER_HEARTBEAT_SECONDS` | Required positive finite number | Worker modes; shorter than lease |
| `MD_CONVERTER_WORKER_INCOMPLETE_SUBMISSION_SECONDS` | Required positive finite number | Worker modes |
| `MD_CONVERTER_WORKER_IDLE_POLL_SECONDS` | Required positive finite number | Worker modes |
| `MD_CONVERTER_WORKER_ERROR_BACKOFF_SECONDS` | Required positive finite number | Worker modes |
| `MD_CONVERTER_WORKER_CLEANUP_INTERVAL_SECONDS` | Required positive finite number | Worker modes |
| `MD_CONVERTER_WORKER_CLEANUP_BATCH_SIZE` | Required positive integer | Worker modes |
| `MD_CONVERTER_WORKER_METRICS_BIND_HOST` | `127.0.0.1` | External worker; valid printable ASCII host |
| `MD_CONVERTER_WORKER_METRICS_PORT` | `9464` | External worker; 1–65535 |
| `MD_CONVERTER_WORKER_METRICS_MAX_CONNECTIONS` | `4` | External worker; 1–64 |
| `MD_CONVERTER_WORKER_METRICS_OBSERVATION_LIMIT` | `2` | External worker; no greater than connections |
| `MD_CONVERTER_WORKER_METRICS_ACCEPT_QUEUE_SIZE` | `8` | External worker; 1–128 |
| `MD_CONVERTER_WORKER_METRICS_REQUEST_TIMEOUT_SECONDS` | `2.0` | External worker; positive finite number |

The API-only distributed process still validates the assembled shared settings at startup. Match
the memory and ephemeral-storage configuration exactly to the worker container limits.

## Templates, scanning, and readiness

| Environment variable | Requirement or default | Applies to / constraint |
| --- | --- | --- |
| `MD_CONVERTER_TEMPLATE_MAX_ARCHIVE_BYTES` | Required positive integer | Both profiles |
| `MD_CONVERTER_TEMPLATE_REQUEST_MAX_BYTES` | Required positive integer | Both; must exceed archive limit |
| `MD_CONVERTER_TEMPLATE_METADATA_REQUEST_MAX_BYTES` | Required positive integer | Both |
| `MD_CONVERTER_TEMPLATE_MAX_NAME_CHARACTERS` | Required positive integer | Both |
| `MD_CONVERTER_TEMPLATE_MAX_DESCRIPTION_CHARACTERS` | Required positive integer | Both |
| `MD_CONVERTER_TEMPLATE_MAX_ENTRIES` | Required positive integer | Both |
| `MD_CONVERTER_TEMPLATE_MAX_MEMBER_BYTES` | Required positive integer | Both |
| `MD_CONVERTER_TEMPLATE_MAX_TOTAL_BYTES` | Required positive integer | Both |
| `MD_CONVERTER_TEMPLATE_MAX_COMPRESSION_RATIO` | Required number, at least 1 | Both |
| `MD_CONVERTER_TEMPLATE_MAX_XML_ELEMENTS` | Required positive integer | Both |
| `MD_CONVERTER_TEMPLATE_MAX_XML_DEPTH` | Required positive integer | Both |
| `MD_CONVERTER_TEMPLATE_MAX_XML_ATTRIBUTES` | Required positive integer | Both |
| `MD_CONVERTER_TEMPLATE_MAX_DECLARED_FONTS` | Required positive integer | Both |
| `MD_CONVERTER_TEMPLATE_MAX_FONT_NAME_CHARACTERS` | Required positive integer | Both |
| `MD_CONVERTER_TEMPLATE_PANDOC_EXECUTABLE` | Required nonblank path/name | Both; locked local executable |
| `MD_CONVERTER_TEMPLATE_LIBREOFFICE_EXECUTABLE` | Required nonblank path/name | Both; locked local executable |
| `MD_CONVERTER_TEMPLATE_ENGINE_TIMEOUT_SECONDS` | Required positive number | Both |
| `MD_CONVERTER_TEMPLATE_ENGINE_TERMINATION_GRACE_SECONDS` | Required positive number | Both |
| `MD_CONVERTER_TEMPLATE_PENDING_PUBLICATION_STALE_SECONDS` | Required positive finite number | Both |
| `MD_CONVERTER_TEMPLATE_VERSION_RETENTION_SECONDS` | `31536000` | Both |
| `MD_CONVERTER_TEMPLATE_MIN_RETAINED_VERSIONS` | `10`, minimum 10 | Both |
| `MD_CONVERTER_AUDIT_RETENTION_SECONDS` | `31536000` | Both |
| `MD_CONVERTER_READINESS_TIMEOUT_SECONDS` | Required positive finite number | API modes in both profiles |
| `MD_CONVERTER_TEMPLATE_ENGINE_WORKSPACE_ROOT` | Optional path | Both; bounded workspace parent |
| `MD_CONVERTER_CLAMAV_HOST` | `127.0.0.1` | Both; set service host explicitly in deployment |
| `MD_CONVERTER_CLAMAV_PORT` | `3310` | Both; 1–65535 |
| `MD_CONVERTER_CLAMAV_TIMEOUT_SECONDS` | `5.0` | Both; positive finite number |

ClamAV scanner unavailability fails closed. Network policy should permit only the configured scanner
path, and credentials or document content must never be placed in these settings.

## Storage profiles and secrets

| Environment variable | Requirement | Applies to / handling |
| --- | --- | --- |
| `MD_CONVERTER_STORAGE_PROFILE` | Required: `standalone` or `distributed` | Selects one coherent profile |
| `MD_CONVERTER_STANDALONE_DATA_DIRECTORY` | Required for standalone; forbidden for distributed | Persistent SQLite and object root |
| `MD_CONVERTER_DISTRIBUTED_DATABASE_URL` | Required for distributed; forbidden for standalone | Secret PostgreSQL URL |
| `MD_CONVERTER_S3_BUCKET` | Required for distributed; forbidden for standalone | Bucket name |
| `MD_CONVERTER_S3_ENDPOINT_URL` | Optional distributed setting; forbidden for standalone | AWS S3-compatible endpoint |
| `MD_CONVERTER_S3_REGION` | Optional distributed setting; forbidden for standalone | AWS region |
| `MD_CONVERTER_S3_ACCESS_KEY_ID` | Optional only with secret key; forbidden for standalone | Secret static credential |
| `MD_CONVERTER_S3_SECRET_ACCESS_KEY` | Optional only with access key; forbidden for standalone | Secret static credential |

Omitting both static S3 credentials allows an AWS-compatible workload credential provider. Inject
all secrets through the platform's secret mechanism; do not put them in images, ConfigMaps,
manifests, commands, logs, or documentation examples.

See [storage profiles](storage-profiles.md) for data layout and [resource policy](resource-policy.md)
for runtime interactions.
