# Architecture

Markweave currently uses a server-rendered FastAPI browser interface with durable asynchronous
conversion workers. The approved T58 target replaces only that browser presentation with a
separate Next.js process; FastAPI and the worker/storage architecture remain authoritative. See
[the reviewed Next.js migration architecture](nextjs-migration-architecture.md) for the staged
topology and parity contract. Markweave accepts Markdown, local resources, and validated DOCX
reference templates; Pandoc creates DOCX, local Mermaid CLI and sandboxed Chromium render diagrams,
and headless LibreOffice creates PDF.

## Component boundaries

- The HTTP and Web layer authenticates local users, enforces same-origin and CSRF rules, validates
  request structure, serves the browser interface, and exposes job state.
- The application layer coordinates account, template, conversion, audit, and cleanup workflows.
- Domain code defines immutable identifiers, authorization, template versions, job states, leases,
  cancellation, and stable failures without depending on a storage product.
- Repository and object-store ports have SQLite/atomic-filesystem and PostgreSQL/S3-compatible
  adapters with shared contract tests.
- Document adapters run ClamAV, archive/image validation, Mermaid, Pandoc, and LibreOffice behind
  fixed argument vectors, allowlisted environments, bounded workspaces, deadlines, and structural
  output validation.
- Workers claim durable leases, heartbeat, load the frozen owner-scoped source, resolve the exact
  template version when selected or use Pandoc's default reference document, publish output
  atomically, and recover interrupted work deterministically.

After T64, the Web presentation is a stateless rootless Next.js process behind the same public TLS
router as FastAPI. Literal path routing sends browser pages and `/_next/**` to that process and
both exact `/api/v1` and `/api/v1/**`, plus every public operational route, directly to FastAPI.
Browser JavaScript uses only relative same-origin API URLs. Regardless of method, the router strips
`Cookie` from every request selected for the frontend, including named pages, assets, and unknown
catch-all paths, and strips every `Set-Cookie` field from every frontend response. It preserves both
directions unchanged for direct FastAPI routes. Next.js has no persistence or
infrastructure credentials, receives no upload/download body, and cannot implement or proxy
FastAPI business behavior.

The conversion path is:

```text
HTTPS request -> authenticated API -> durable source + job -> leased worker
              -> validated local workspace -> DOCX -> optional PDF
              -> atomic result + traceability manifest -> authenticated download
```

The API never converts inline. Submission persists immutable input before a worker can claim the
job. Result visibility follows the committed job state, so a partial object is not exposed as a
completed conversion.

## Storage and process profiles

Standalone uses one `embedded-worker` process, a SQLite database, and atomic files under `/data`.
It is restricted to one replica. The database and object tree form one backup and recovery unit.

Distributed separates horizontally scalable `api` processes from `external-worker` processes. It
requires PostgreSQL and an AWS S3-compatible bucket. PostgreSQL claims use row locking and
`SKIP LOCKED`; immutable objects use conditional S3 operations. RustFS is the CI implementation of
that interface, not an application-specific dependency.

Both profiles share the same domain and storage contracts. Profile selection is explicit at startup
and rejects mixed SQLite/S3 or PostgreSQL/filesystem configuration. See
[storage-profiles.md](storage-profiles.md).

The target standalone deployment adds one frontend replica to the existing single backend/embedded
worker replica. The target distributed deployment scales stateless frontend replicas independently
from API replicas and external workers. Frontend failure therefore cannot corrupt backend state,
and API/CLI availability is independent of browser-page availability.

## Templates and authorization

A template identity has an immutable owner and immutable content versions. Replacement and restore
publish a new version atomically. Visibility-aware queries execute in the database; normalized
search behavior remains profile-neutral. Per-user preference resolution falls back to the active
system template. Archive and deletion enforce ownership, administrator intervention, retention, and
referential-integrity rules. Security-sensitive administrator actions are audited.

Accounts carry an authentication version. Password reset, disable, reactivation, and other security
mutations increment it atomically, invalidating sessions that captured an older version. Expensive
Argon2 verification remains outside the database transaction, while compare-and-set mutations
prevent verification/reset races.

## Security model

All browser and API use is authenticated and intended for HTTPS. State changes require a CSRF token
and a matching effective origin. With `MARKWEAVE_PUBLIC_ORIGIN` set, that exact external
scheme/host/optional-port is authoritative for Origin checks; the value must contain no path, query,
fragment, or user information. When it is unset, the direct ASGI request base URL is authoritative.
Proxy forwarding headers remain deliberately untrusted.

The server-side Next.js process cannot read or forward any browser cookie because the router
removes the complete `Cookie` header before forwarding any frontend route or method. The router
also removes all frontend `Set-Cookie` response fields. Next.js renders a public shell and lets
browser JavaScript obtain session state directly from FastAPI and read only the CSRF cookie required
for mutations. A nonce-based CSP, no-store HTML/API responses, immutable content-hashed assets, and
direct FastAPI upload/download routing keep the new presentation boundary from becoming a
credential, file, or cache boundary.

Document-controlled network access is forbidden. Remote Markdown, image, CSS, font, and Mermaid
references are rejected; the only runtime egress is to explicitly configured infrastructure such as
ClamAV, PostgreSQL, and S3-compatible storage. Engines receive fixed local paths and no shell.

The final image runs as an arbitrary non-root UID with no capabilities, `no-new-privileges`, a
read-only root filesystem, dedicated bounded `/tmp`, `/work`, and `/dev/shm`, and the reviewed
Chrome seccomp profile on worker nodes. Input, expansion, structural, process, duration, memory,
ephemeral-storage, queue, and retention ceilings are independent. Values unresolved by section 14
of the product specification remain operator-required configuration, not hidden defaults.

## Traceability and observability

Correlation, user, job, template, version, and audit identifiers cross the API, worker, and output
manifest boundaries without logging document content. DOCX embeds traceability; PDF uses a canonical
sidecar; combined output packages both documents and `traceability.json`. API metrics are exposed
per API process and worker metrics per external-worker process. Readiness verifies profile
dependencies and, in standalone, embedded-worker health.
