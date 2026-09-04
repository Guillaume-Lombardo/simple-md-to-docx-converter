# Markdown to DOCX and PDF Converter

**Status:** Functional, technical, and autonomous-development specification

**Date:** September 3, 2026

**Runtime target:** UBI 9 container, Python 3.14, rootless Podman, and OpenShift

**Forge and CI:** GitHub, GitHub Actions, and trunk-based development

## 1. Objective

Build a Web service that converts Markdown documents to DOCX, PDF, or both and, through an
experimental reverse workflow, converts supported office documents to structured Markdown through
a browser interface and a documented HTTP API.

The service accepts a standalone `.md` file or a `.zip` archive containing Markdown and local resources. Users may select an administrable Word template or use Pandoc's native default reference document. The service resolves and normalizes local images, renders Mermaid diagrams locally, applies the selected document-style mode, and retains source and result files only for the configured asynchronous-processing and download period.

The product includes Convert and experimental Revert workspaces, template administration, local
authentication, two configurable storage profiles, a hardened UBI 9 image, selective GitHub
Actions workflows, and an autonomous Codex development workflow.

## 2. Fixed decisions

| Topic | Decision |
|---|---|
| Backend language | Python 3.14 |
| Backend image | Official `ubi9/python-314`, pinned by digest in CI |
| API boundary | FastAPI remains the sole authority for business behavior, authentication, authorization, persistence, conversions, templates, accounts, audit, health, readiness, metrics, and OpenAPI under exact `/api/v1`, `/api/v1/**`, and the documented operational routes |
| Web migration target | A Next.js, TypeScript, and Tailwind CSS application under `web/` owns browser pages and assets after the staged T58–T64 migration; the existing FastAPI-rendered frontend remains active until verified cutover |
| Browser boundary | Browser pages and both exact `/api/v1` and `/api/v1/**` use one public origin; Next.js route handlers must not duplicate FastAPI business or authorization rules |
| Frontend topology | A separate stateless rootless Next.js process serves browser pages behind the same TLS router as FastAPI; browsers call FastAPI directly through relative same-origin exact `/api/v1` or `/api/v1/**` URLs, and the frontend has no business-service or persistence credentials |
| Frontend runtime baseline | Linux/AMD64 UBI 9 Node.js 24 builder and minimal runtime pinned by digest, resolving to Node.js `24.19.0`; verified Corepack `0.36.0` selects pnpm `11.25.0`; Next.js `16.3.4`, TypeScript `6.0.3`, and Tailwind CSS `4.3.3` remain exact and root-lockfile-integrity pinned as reviewed on 2026-09-03 |
| Processing | Asynchronous jobs with a persistent queue and status API |
| Markdown to DOCX | Pandoc |
| Mermaid | Local Mermaid CLI and Chromium |
| DOCX to PDF | LibreOffice headless |
| Documents to Markdown | Local `firecrawl-anydoc==0.2.4` with no hosted fallback; the exact x86-64 ABI3 wheel, source commit, format matrix, and limitations are pinned by T69 |
| Reverse-conversion output | Structured UTF-8 Markdown with deterministic safe relative image links; plain Markdown only when no embedded or unavailable image position exists, otherwise a deterministic ZIP carries Markdown, normalized referenced assets when available, and the content-free manifest |
| Reverse-conversion OCR | No OCR; scanned or image-only documents fail locally with a stable safe error, and adding an OCR service requires separately approved future scope |
| Reverse-conversion compute | CPU-only and low-compute with bounded threads and concurrency; do not use a GPU, ML model, browser, Pandoc, LibreOffice, or another document engine |
| Reverse-conversion execution isolation | A trusted external isolation broker, separate from the application worker and any attempt unit, exclusively owns Podman or Kubernetes workload authority. Through a narrow authenticated Unix-socket or mutually authenticated TLS protocol, the worker-side supervisor requests one immutable-image, fixed-argument disposable unit per attempt. The child receives bounded local input/output only, has no network, service-account token, secret, ConfigMap, PVC, persistence credential, raw OCI socket, or publication capability, and runs the anydoc native call in-process under kernel-enforced CPU, memory, PID/descendant, workspace/ephemeral, and autonomous runtime-deadline limits. A mandatory bounded crash-consistent content-free broker inventory records identity before creation and retains a termination tombstone until durable worker/T71 acknowledgement; runtime labels are supplementary only. The broker refuses readiness and creation until its idempotent sweep completes, and worker recovery remains blocked until proof is durably recorded. |
| Reverse-conversion asset serialization | Use one narrowly bounded maintained internal adapter around the pinned anydoc document model and renderer behavior; prohibit a second parser or broad fork, require security/parity/compatibility/SBOM/license ownership, and remove it when upstream provides a supported asset-aware hook |
| Runtime | Rootless Podman and arbitrary-UID OpenShift compatibility |
| Forge and CI | GitHub and GitHub Actions |
| Python distribution | `markweave` on PyPI with public import `markweave`; availability must be rechecked immediately before the first publication attempt, and a pending Trusted Publisher does not reserve the name |
| Command line | The installed `markweave` executable exposes every supported HTTP user/administrator operation and the local `serve`, `worker`, `doctor`, `migrate`, `backup`, and `restore` operational commands |
| CLI boundary | Business commands use only the documented HTTP API; only operational commands may access runtime or storage services directly |
| CLI authentication | Remote commands reuse the existing session and CSRF contract, prompt passwords without echo or process arguments, and keep only bounded session state in owner-only XDG files; no API token is introduced |
| Configuration names | `MARKWEAVE_*` is canonical; legacy `MD_CONVERTER_*` aliases remain supported throughout 0.x, conflicting dual definitions fail closed, and aliases are removed in 1.0 |
| Git workflow | Trunk-based, short branches, pull requests to `main`, squash merge |
| Python dependencies | `uv` |
| Quality tools | Ruff, `ty`, Pytest, pytest-cov, and pytest-mock |
| Coverage | At least 90% of application Python and 90% of changed Python lines |
| Standalone storage | SQLite and files on a PVC, one replica |
| Distributed storage | PostgreSQL and S3-compatible object storage |
| Initial authentication | Local username/password behind an OIDC-ready abstraction |
| Document resources | Remote-loaded resources are forbidden; validated HTTP(S) hyperlinks are allowed |
| Markdown reader | `commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html`, with raw HTML rejected before Pandoc |
| Browser sandbox | Keep Chromium's sandbox enabled; validate a minimal seccomp/user-namespace profile on rootless Podman, then k3s; never use `--no-sandbox`; defer OpenShift proof |
| Document fonts | Liberation plus Carlito/Caladea, DejaVu as fallback, and Noto only for explicitly required scripts |
| Distributed test object store | RustFS for CI and k3s, behind a provider-neutral AWS S3-compatible contract |
| Repository language | English for code, identifiers, docstrings, UI, errors, logs, documentation, commits, and pull requests |
| Superseded template retention | 365 days; never delete the active version or ten newest versions per template |
| Audit retention | Immutable for 365 days, then bounded traceable deletion |
| Upload malware scanning | Local ClamAV before processing or durable persistence by default; an explicit trusted-upstream mode is permitted only behind a non-bypassable proxy that scans every upload before forwarding; the explicit insecure evaluation exception may omit scanning only while loopback-bound behind a temporary SSH tunnel; fail closed in ClamAV mode; no durable quarantine |
| Recovery targets | Standalone RPO 24h/RTO 4h; distributed RPO 1h/RTO 2h; automated quarterly proof |

Asynchronous processing avoids coupling job duration to browser, OpenShift Route, and application request timeouts. It provides bounded concurrency, restart recovery, state tracking, and one contract for both storage profiles. No extra queue or message broker is used: SQLite carries the standalone queue and PostgreSQL carries the distributed queue. The reverse-conversion isolation broker defined below is an execution-control boundary only; it never stores, claims, schedules, or publishes durable jobs.

## 3. Functional requirements

### 3.1 Conversion jobs

- Accept `.md` and `.zip` uploads.
- Select the main Markdown file deterministically.
- Validate an archive before extraction and resolve only safe relative resources.
- Support the approved Pandoc-compatible headings, lists, tables, links, footnotes, quotes, code blocks, image attributes, and metadata.
- Render every Mermaid block locally.
- Use either Pandoc's native default reference document or an active immutable template version.
- Produce DOCX, PDF, or a ZIP containing both.
- Name each result from the uploaded source filename stem, replacing only the final source extension
  with `.docx`, `.pdf`, or `.zip`, and encode the attachment header safely.
- Return a job identifier immediately and expose state, current step, progress, safe errors, cancellation, result download, and expiration.
- Use states `queued`, `running`, `succeeded`, `failed`, `cancelled`, and `expired`.
- Return the component versions and explicit template mode needed to reproduce a conversion; a
  versioned-template job also returns its immutable template identifiers.

### 3.2 Word templates

- Derive an immutable owner from the authenticated identity.
- Make every active template searchable, downloadable, and usable by authenticated users.
- Allow mutations only by the owner or a global administrator; audit every administrator intervention.
- Support paginated search and filters by name, description, owner, and status.
- Validate DOCX content before activation.
- Support rename, description update, download, atomic replacement, immutable version history, copy-forward restoration, archive, and guarded deletion.
- Maintain a preferred template per user and a system fallback template.
- Use `ETag` and `If-Match` for concurrent mutations.

### 3.3 Web interface

Provide a login page and three main browser workflows. The target implementation is the Next.js,
TypeScript, and Tailwind CSS application under `web/`; the current server-rendered pages remain the
production implementation until T64 completes parity, rootless E2E verification, and cutover:

- **Convert:** upload or drag-and-drop, choose Pandoc's default or search and select a template,
  choose output, create a job, poll with progressive backoff, cancel, inspect status, download, and
  display accessible English errors.
- **Templates:** list visible templates and owners, filter “my templates,” create, download, rename, replace, restore, delete, and choose the preferred template.
- **Revert (Experimental):** upload or drag-and-drop a supported office document, create a local
  document-to-Markdown job, poll with progressive backoff, cancel, inspect status, download the
  Markdown result or asset package, and display accessible English errors. The navigation label has
  a visible stamp-style `Experimental` treatment whose meaning is also available to assistive
  technology.

Administrators also receive controls to create, search, activate, deactivate, and reset local
accounts and to configure the effective system-wide, role-specific idle session durations. Every permission and
session-expiry decision is enforced by FastAPI. Application TypeScript and JavaScript receive their
own tests and blocking coverage checks.

#### 3.3.1 Next.js production and migration contract

The reviewed routing boundary sends both exact `/api/v1` and `/api/v1/**`, plus `/health/live`,
`/health/ready`, `/metrics`, `/docs`, `/docs/**`, `/redoc`, and `/openapi.json`, directly to FastAPI.
At T64 cutover it sends `/`, `/login`, `/change-password`, `/convert`, `/templates`, `/revert`, and
`/_next/**` to Next.js. Unknown paths return the
frontend's accessible `404`; they never fall through to FastAPI or an external destination. The
router preserves `Host` and `Origin`, rejects unknown hosts, does not trust forwarded headers, and
never selects an upstream from user-controlled headers. No CORS or second browser origin is
supported. Before the frontend catch-all, the public router returns a content-free `404` for
`/_frontend/health`, every descendant, and decoded or case-varied equivalents. Only platform probes
may call the exact internal `/_frontend/health/live` and `/_frontend/health/ready` paths through the
frontend Service's separate probe port; the public router reaches only the page port.

The router removes the complete `Cookie` request header before every request whose selected upstream
is the frontend, regardless of method or route class, including named pages, `/_next/**`, and the
unknown-path catch-all. It removes every `Set-Cookie` response-header field from every frontend
response regardless of method, status, or content type. Exact `/api/v1`, `/api/v1/**`, and the public
operational routes go directly to FastAPI without either transformation, preserving its incoming
`Cookie` and all outgoing `Set-Cookie` fields. T60 blocks on routing-fixture tests for both
directions, named pages, assets, unknown paths, and non-GET requests; T64 repeats them through the
production router against the exact final images.

Next.js is a presentation-only process. Browser code calls same-origin FastAPI routes directly.
Except for two non-public process-local frontend probes, route handlers, Server Actions,
Proxy, rewrites, server components, and server-side fetches
must not proxy API requests, read or forward session credentials, or reproduce authentication,
authorization, validation, quotas, persistence, conversion, template, account, audit, health,
readiness, metrics, or OpenAPI behavior. The frontend receives no database, object-store, scanner,
worker, or document-engine credentials and mounts neither `/data` nor `/work`.

FastAPI exclusively owns the public health/readiness contract. The frontend exposes only internal
`/_frontend/health/live` and `/_frontend/health/ready` platform probes; readiness verifies its
immutable built route/assets and never calls FastAPI or storage. Browser-route availability and
FastAPI readiness are monitored separately. Frontend or backend failure must not cascade into
state corruption: API/CLI use can continue during a frontend outage, while backend loss produces
bounded safe browser errors without client-side mutation replay.

The frontend runs with an arbitrary non-zero UID, group 0, read-only root, no capabilities,
`no-new-privileges`, RuntimeDefault seccomp, no service-account token, and only a `32Mi`
memory-backed `/tmp`. Each replica requests `100m` CPU, `128Mi` memory, and `32Mi` ephemeral
storage and is limited to `500m` CPU, `256Mi` memory, `64Mi` ephemeral storage, a `160Mi` Node.js
old-space heap, 64 processes, 128 in-flight requests, 16 KiB request headers, and 30 seconds for
graceful shutdown. Raising a hard ceiling requires reviewed load evidence.

T60 owns `web/server.mjs`, a supported Next.js custom production server. It uses Node's HTTP server
with `maxHeaderSize: 16384` and a zero-length header-overflow `431`, admits at most 128 requests per
replica, returns a zero-length `503` before Next.js when saturated or draining, accounts each
response exactly once on finish or close, and completes admitted work for at most 30 seconds after
SIGTERM. It performs no API proxying or business work. Because Next.js does not support custom
servers with `output: "standalone"`, the
frontend packages the `.next` build and exact pruned production dependency graph instead. T60 and
T64 must block on the exact 128/129 admission boundary, header rejection, empty failure responses,
finish/close accounting, drain races, and bounded shutdown.

Every dynamically rendered HTML document response is `no-store` and uses a fresh per-response
cryptographic nonce with a CSP allowing only self-hosted connections, stylesheets, fonts, and images
plus nonce-bearing framework scripts and inline style elements; it forbids
objects, framing, external resources, workers, `unsafe-inline`, and `unsafe-eval`. Authenticated
API responses and downloads remain uncacheable. Only content-hashed `/_next/static/**` assets may
use one-year immutable caching. No service worker, CDN data cache, analytics, remote font, or
third-party script is allowed. On exact Next.js `16.3.4` production dynamic rendering, T60 and T64
must prove a fresh nonce for each HTML document with no cache reuse, the same nonce in `script-src`
and `style-src`, a nonce on every framework bootstrap script and inline style element, no inline
style attribute or unnonced inline style, and no eval requirement. Content-hashed
`/_next/static/**` assets and non-HTML or content-free responses, including custom-server header
error, saturation, and draining responses, are outside nonce generation and freshness assertions.
T60 implements the interception hook as `web/proxy.ts` with the named `export function proxy`;
structural and production-build tests reject the deprecated `middleware.ts` filename or
`middleware` export.

The public TLS router adds exactly `Strict-Transport-Security: max-age=31536000` and
`Permissions-Policy: camera=(), geolocation=(), microphone=(), payment=(), usb=()` to every HTTPS
response. T64 verifies the values on frontend, FastAPI, error, and download responses. HSTS
`includeSubDomains` and `preload` are excluded because Markweave does not control every sibling
subdomain or the parent domain's browser preload commitment.

FastAPI keeps the opaque `HttpOnly`, `Secure`, `SameSite=Lax`, path-root session cookie and the
separate JavaScript-readable `__Host-md_converter_csrf` cookie. Browser mutations copy the latter
into `X-CSRF-Token`; login goes directly to `/api/v1/login` and preserves exact-Origin validation.
Although the browser sends same-origin cookies on matching frontend paths, the router strips the
complete request header before Next.js and strips every frontend-originated `Set-Cookie` response
field. It preserves both header directions unchanged on direct FastAPI routes.
Uploads and downloads pass router-to-FastAPI without traversing Next.js, and retain FastAPI's
scanner, authorization, filename, digest, content-type, `nosniff`, and private no-store contracts.

The root browser-test package and `web/` use one pnpm workspace and one root `pnpm-lock.yaml`.
Workspace membership explicitly includes only the repository root and `web/` and explicitly
excludes `spikes/toolchain`; an automated negative-membership test enforces that boundary. The
isolated Mermaid production graph remains npm-based, retains its independent lock and exact Mermaid
version, and is installed only with `npm ci --prefix spikes/toolchain --omit=dev --ignore-scripts`.

The frontend uses digest-pinned UBI 9 Node.js 24 builder and minimal runtime images. Node.js
`24.19.0`, Corepack `0.36.0`, pnpm `11.25.0`, the integrity-bound root `packageManager` selection,
and every direct dependency are exact. Bootstrap downloads Corepack only from its immutable exact
npm-registry tarball URL, verifies the reviewed SHA-512 before installing it, and asks that verified
Corepack to acquire pnpm only under the reviewed package-manager integrity hash. Every later command
sets `COREPACK_ENABLE_NETWORK=0`; a mismatch or unavailable verified byte fails closed instead of
activating a package manager implicitly.

Deterministic installation uses `pnpm install --frozen-lockfile --ignore-scripts`. The root lock
pins the complete integrity-checked application and browser-test graph and preserves the reviewed
npm-baseline package/version set, peer resolution, and overrides. CI caches only pnpm's
content-addressable store under a key containing the operating system, exact Node.js version, exact
pnpm version, and lock digest; untrusted contexts may restore but never write caches. Frontend image
construction uses the repository root context, builds from the frozen workspace graph, and copies
only a target-platform `pnpm deploy --prod --legacy` graph into the runtime. Corepack, pnpm, their
caches, development dependencies, and package-manager network access are absent at runtime.
Updates occur only through reviewed pull requests with support, license, vulnerability, build,
browser, rootless, cold-cache, benchmark, and rollback evidence. No production build or startup
resolves a tag, range, Git URL, CDN, or dynamically downloaded asset. Historical release-evidence
recovery binds the root pnpm lock when the release source contains one and otherwise binds the
legacy frontend npm lock, so old exact bytes remain recoverable without rebuilding.

Final releases bind one source SHA and version to the PyPI package plus distinct backend and
frontend GHCR manifest digests. Each image is built and serialized once and receives CycloneDX and
SPDX SBOMs, vulnerability evidence, a publication receipt, and provenance for its public digest.
Deployment manifests pin both exact digests and prohibit mixed frontend/backend releases. Partial
publication is recovered from retained exact bytes without rebuilding or is treated as a failed
release.

T60–T63 left the legacy FastAPI pages on the production route while building and verifying
the frontend foundation, authentication, conversion, and administration parity. T64 completed
parity and rollback rehearsal while the candidate branch still contained the legacy renderer, then
removed the legacy code and assets. The release process builds and serializes the final matched
images exactly once and runs the complete two-profile rootless acceptance matrix against those
staged bytes. It publishes and deploys only those verified bytes; no post-verification removal or
rebuild is permitted. Cutover switches the route to their exact published digests. Rollback restores
the previous matched backend release with its legacy UI and route manifest; if persistent schema or data changed, it
also restores the matching pre-cutover database and object backup according to the upgrade
contract. Mixed releases and mutable rollback tags are forbidden. The complete inventory and
failure matrix are in [the reviewed migration architecture](nextjs-migration-architecture.md).

### 3.4 Command-line interface

Installations and final containers expose the same `markweave` executable. Its remote-client
commands cover login, logout, session inspection, conversion submission, job lifecycle and
downloads, template discovery and administration, user administration, audit, health, readiness,
metrics, and self-service password renewal, including restricted sessions that require renewal.
These commands use only the documented HTTP API and preserve the same authorization,
CSRF, optimistic-concurrency, idempotency, pagination, retry, and safe-error contracts as browser
and direct API clients.

The local `serve`, `worker`, `doctor`, `migrate`, `backup`, and `restore` commands may assemble the
runtime or access the selected storage profile directly. They reuse application services instead
of duplicating business rules, validate one coherent profile, redact secrets, support deterministic
non-interactive execution, and retain arbitrary-UID and read-only-root behavior in the final image.

`markweave login` prompts a password through a non-echoing terminal path and never accepts it as a
process argument. Named remote profiles keep the service URL, opaque session state, and CSRF state
under the XDG directories in atomic owner-only `0600` files. They never retain the password. TLS
verification is enabled by default. API tokens are outside this delivery scope.

T31 owns the root command registry and pre-registers stable, initially unavailable command-family
modules for authentication, conversions/jobs, templates, administration/audit/health, runtime
operations, and recovery operations. T36 exclusively fills the runtime-operations family for
`serve`, `worker`, `doctor`, and `migrate` in `src/markweave/cli/commands/runtime.py`; T37
exclusively fills the recovery-operations family for `backup` and `restore` in
`src/markweave/cli/commands/recovery.py`. Downstream CLI tickets replace only their assigned family
implementation and tests;
they do not edit the root registry or shared help snapshots. T50 owns the final cross-family help
and end-to-end integration snapshots.

### 3.5 Experimental reverse conversion

Reverse conversion is an authenticated asynchronous workflow separate from forward conversion. It
uses a pinned local Firecrawl anydoc engine and never opts into Firecrawl Parse, hosted OCR, or any
other network fallback. T69 approves the exact supported format and content-detection matrix recorded
by its evidence contract. Scanned or image-only inputs that require OCR fail locally with a stable
safe error.

T71 exposes that approved matrix and its client-relevant configured constraints through the
authenticated, versioned `GET /api/v1/reversions/capabilities` FastAPI contract. Its response has a
schema-version identifier and authoritative ordered format families, extensions, content-detection
rules, maximum upload bytes, result-package modes, PDF limitations, and local/no-OCR flags. The
Next.js workspace derives its format hint, file chooser, and bounded preflight validation from this
response; it carries no duplicate format matrix or hardcoded fallback and disables submission with
a safe backend-unavailable state when capabilities cannot be loaded. Server-side detection and
validation remain authoritative regardless of client preflight.

The production path is CPU-only and optimized for low compute. It must not discover, request, or
use a GPU or accelerator, load an ML model, start a browser, invoke Pandoc or LibreOffice, or spawn
another document-engine process. T69 measures cold and warm wall time, CPU time, peak resident
memory, retained asset bytes, and concurrency scaling on representative and configured-limit inputs.
Those measurements determine reviewed configurable budgets and a bounded thread/concurrency policy;
no unmeasured numeric threshold is fixed in this specification.

The synchronous native call runs in-process only inside one disposable Podman container or
Kubernetes workload placed in a dedicated stable kernel isolation unit for one attempt. A trusted
isolation broker outside both the application worker and the attempt unit exclusively holds the
Podman or Kubernetes workload authority needed to create, constrain, inspect, terminate, and
remove that unit. The application and child never receive a raw OCI socket or workload-mutating
Kubernetes service account.

The worker-side attempt supervisor owns the durable attempt token and lease heartbeat, sends one
bounded request to the broker, accepts one bounded result, revalidates both before publication,
and remains the only publisher. Co-located deployments use an owner-restricted Unix socket with
peer authentication; separated deployments use mutually authenticated TLS with pinned service
identities. The protocol exposes only fixed operations and content-free stable attempt/unit
identifiers. User or document input cannot select an image, executable, argument, mount, network,
credential, namespace, security profile, or resource ceiling.

The broker launches only the reviewed image pinned by immutable digest and its fixed
reverse-attempt argument vector. The child receives bounded local input and output only; it has no
network access, service-account token, Secret, ConfigMap, PVC, database or object-store credential,
broker credential, runtime socket, or publication capability. Podman runs it with no network and a
fresh bounded workspace. Kubernetes runs it with service-account automount disabled, service links
disabled, no secret-bearing or persistent volume, and enforced default-deny egress. Both backends
apply the same capability-free, no-new-privileges, read-only-root, arbitrary-UID contract. T71
supplies reviewed configurable CPU, memory, PID/descendant, and workspace or ephemeral-storage
budgets, and the broker enforces them at the runtime/kernel boundary. Userspace sampling is
observability, not containment.

At creation, the broker applies the reviewed T71-configured wall-time deadline through the runtime
itself, so an attempt is terminated even if the worker or broker process crashes. The broker keeps
a mandatory bounded crash-consistent content-free managed-unit inventory. It durably records the
broker-authored stable identity before asking the runtime to create a unit. Immutable
broker-authored runtime labels supplement inventory-based discovery and verification but never
replace it. Inventory identities, labels, and lifecycle fields are authenticated and integrity-
protected broker output, contain no document data or secret, and cannot be supplied or modified by
user or document input. On startup and reconnect, the broker idempotently reconciles and sweeps
every inventoried unit, hard-kills any orphan, and proves it empty and removed. It reports not ready
and refuses every new create request until this sweep completes successfully.

On cancellation, wall-time deadline, lease loss, broker disconnect, or a hard resource-limit event,
the worker stops accepting child output and requests termination. The broker hard-kills the whole
stable unit, waits for runtime-confirmed exit, proves the unit empty, removes it, and returns a
content-free termination proof bound to the stable unit identity. Observing a recorded PID exit or
a successful delete request is insufficient. The lease cannot become recoverable and another
attempt cannot start until T71 durably records that proof. If proof is unavailable, recovery remains
blocked and readiness/operations expose a content-free fault. After normal completion, the broker
proves unit termination before the worker validates the bounded output and revalidates the active
lease and attempt token for publication. Python timeouts, cancellation flags, userspace resource
sampling, or publication fencing alone remain insufficient.

The broker durably records runtime-confirmed exit and empty transitions before removal, then records
the removed transition before returning proof. A missing runtime object, delete acknowledgement, or
Kubernetes force-delete response alone is never proof. If the broker crashes after kill or removal
but before returning proof, idempotent restart reconciliation resumes from the inventory and runtime
state. The content-free termination tombstone is retained until the worker/T71 durably acknowledges
the proof. The worker keeps recovery blocked until T71 has durably recorded it.

For formats whose approved parser exposes a structured document model, preserve supported
headings, lists, tables, links, notes, code, equations, and document order. Export every supported
embedded image reported at a source position under deterministic safe `assets/` paths and reference
it from the root Markdown with a relative `![]()` link. Never download an external image. Treat
every exported image byte stream as untrusted input: identify it by decoded signature rather than
its source name or declared media type, reject non-image and mismatched/polyglot payloads, enforce
bounded decode dimensions and bytes, reject animated or multi-frame content, and reuse the T08
sanitization and local network-disabled rasterization contract for SVG before deterministic image
normalization. A result with assets is a deterministic ZIP containing exactly one root Markdown
file, its referenced local assets, and content-free traceability metadata. T69 defines the approved
metadata schema; T70 owns its deterministic canonical generation together with the package builder.
T69 decides the asset-free download contract and the honest PDF contract because anydoc's current
PDF path produces Markdown directly without exposing the shared document model or embedded assets.

Because anydoc 0.2.4 exposes no supported renderer hook for an already parsed `Document`, T70 may
maintain one bounded internal compatibility adapter around the exact pinned document-model and
renderer behavior. The adapter consumes the single parsed `Document`; it never reparses source
bytes or introduces a second document parser. One module boundary inventories every private symbol
and any minimally mirrored upstream renderer logic, fails closed for an unknown anydoc version or
model variant, retains applicable upstream license notices, and is included in dependency, SBOM,
license, and vulnerability review. Asset-free structures must pass serializer-parity tests against
the pinned upstream renderer, while asset fixtures prove source-position link injection, safe
normalization, ordering, unavailable-asset behavior, and the closed manifest contract. Every anydoc
upgrade reruns compatibility and parity coverage before adoption. T70 owns maintenance and must
remove this adapter when upstream provides a supported asset-aware renderer hook; an independent
parser or broader serializer fork is not approved.

Reverse jobs preserve the existing scanner ordering, owner isolation, persistent queue states,
idempotency, leases, recovery, cancellation, expiration, capacity, retention, content-free logging,
and both-storage-profile contracts. Their limits and metrics are explicit and cannot silently reuse
forward-conversion values when the workloads differ.

Every `/api/v1/reversions` source, job-status, cancellation, and result operation is owner-only;
global administrator status does not grant document-content access or impersonation. Administrators
may use only the separately authorized operational observability and audit surfaces for content-free
metadata needed to diagnose capacity and execution, such as opaque job identity, owner identity,
state, safe failure category, attempt/lease state, timing, and byte counts. Those surfaces expose no
source or result bytes, original filename, Markdown, asset name or bytes, content-derived digest, or
download capability, and every administrator access follows the existing audit contract.

## 4. Input contract

A standalone Markdown file is accepted only when it has no local-resource dependency. The service
never downloads document images, stylesheets, or other remote resources. Ordinary hyperlink
destinations are not loaded: accept only well-formed absolute HTTP(S) hyperlinks with a host, no
embedded credentials, and no control characters. Reject other URI schemes, protocol-relative
destinations, and encoded-scheme equivalents.

Recommended archive layout:

```text
document.zip
├── document.md
└── assets/
    ├── architecture.png
    ├── diagram.svg
    └── screenshot.jpg
```

Select root `document.md` first, otherwise the sole `.md` file, and reject ambiguity. Reject
absolute and escaping paths, symlinks, encrypted archives, ZIP bombs, abnormal compression ratios,
configured-limit violations, disallowed types, and every remotely loaded resource URL including
internal-network destinations. This resource prohibition includes images even when their URL would
be a valid HTTP(S) hyperlink destination.

Supported image candidates are PNG, JPEG, sanitized SVG, static GIF, and WebP. Reject animated GIF. Treat SVG as untrusted XML: disable external entities, remove scripts and external references, then rasterize locally without network access. Include XXE, script, and remote `xlink:href` fixtures in the security corpus.

## 5. Markdown, images, and document engines

Use the exact Pandoc reader expression
`commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html`. Because Pandoc 3.10.2
can still emit raw HTML nodes with that reader, reject raw HTML in the validated Markdown input
before invoking Pandoc. Disable user filters, user includes, and remote resources. TeX-like text is
ordinary text for this reader; no unsupported `-raw_tex` flag is added.

Evaluate `pandoc --sandbox` in T00 with images and `reference.docx`; do not claim it is enabled until the complete pipeline passes. Run Pandoc from the job workspace with fixed arguments, no shell, no network, deadlines, memory limits, and an unprivileged identity.

Render Mermaid through a locally installed Chromium; Puppeteer must never download a browser during build or runtime. Bound pixel resolution and physical document width/height while preserving aspect ratio. Chromium must work with arbitrary UID, writable HOME/XDG directories, bounded `/dev/shm`, read-only root filesystem, and no network. Keep Chromium's sandbox active and never use `--no-sandbox`. Develop and validate a minimal seccomp and user-namespace profile first on rootless Podman and then on k3s. OpenShift proof is explicitly deferred and remains required before claiming OpenShift compatibility.

Generate DOCX without `--reference-doc` in Pandoc-default mode, or with the exact selected reference
document in versioned-template mode. Generate PDF from DOCX to preserve Word styling. Give every
LibreOffice invocation an isolated temporary user profile and terminate its whole process group on
timeout or cancellation.

Templates declare expected fonts. Package and validate Liberation plus Carlito/Caladea, use DejaVu as
the fallback, and add Noto families only for scripts explicitly required by the approved corpus or
template contract. Validate their licenses, Fontconfig behavior and substitution order, required
Pandoc styles, macros, external OOXML relationships, blank canonical conversion, and LibreOffice
opening before activation.

## 6. Storage and job execution

### 6.1 Standalone profile

Use SQLite plus atomic files under `/data`, one application replica, and an embedded bounded worker. Persist jobs before acknowledging upload. Recover expired leases and unfinished jobs after restart. Never share SQLite between pods.

### 6.2 Distributed profile

Use PostgreSQL for metadata, queue state, leases, heartbeat, and concurrency control; use
S3-compatible object storage for uploads, results, and template versions. Use RustFS in CI and k3s,
while keeping the object-store adapter provider-neutral and compatible with AWS S3. Workers may run
separately. Claim jobs with transactional locking such as `FOR UPDATE SKIP LOCKED`. Prevent
simultaneous duplicate execution.

### 6.3 Shared abstractions

Expose repository and object-store interfaces with the same contract tests for both implementations. Select exactly one coherent profile at startup and fail fast on mixed or incomplete configuration. Manage schemas with Alembic. Document backup and restoration for both profiles.

Persist users, templates, template versions, preferences, conversion jobs, job events or attempts,
and audit records. A conversion job stores either both template identifiers or neither, enforced by
the domain and both databases. Object keys and paths derive only from stable identifiers, never
visible names.

## 7. HTTP API

Use `/api/v1`. The contract must include:

- local login/session and administrator account management;
- `POST /conversions` returning `202 Accepted`, `Location`, and `Retry-After`;
- paginated user-owned conversion listing;
- job status, cancellation, and result download;
- reverse-conversion submission under `/reversions` returning `202 Accepted`, `Location`, and
  `Retry-After`, plus owner-scoped listing, status, cancellation, and Markdown-package download;
- authenticated reverse-conversion capabilities under `GET /reversions/capabilities`, exposing the
  versioned T69-approved format/detection matrix and client-relevant configured constraints;
- active-template search and listing;
- template creation, metadata update, current/previous content download, replacement, version listing, copy-forward restoration, deletion/archive, and per-user default selection;
- `/health/live`, `/health/ready`, metrics, and `/docs`.

Support `Idempotency-Key` for job creation. Preserve the existing forward-conversion authorization
contract. For reverse conversions, enforce the stricter owner-only content and lifecycle contract
in section 3.5; administrator visibility is limited to its separately audited, content-free
operational metadata. Return stable functional error codes without traces or local paths. Keep
readiness cheap; it must not run a conversion.

## 8. Security and operations

- Store local passwords with a reviewed Argon2id configuration. Keep secrets outside the image and repository.
- Enforce owner and administrator authorization on the server for every route and object lookup.
- Bound upload size, decompressed size, file count, image count, diagram count, job duration, memory, ephemeral storage, active jobs per user, and global queue depth.
- Return `429` plus `Retry-After` for user quota exhaustion and `503` plus `Retry-After` for global capacity exhaustion.
- Use unique workspaces, fixed subprocess argument lists, `shell=False`, environment allowlists, process groups, timeouts, cancellation, periodic expiration, and reliable cleanup.
- Run as arbitrary non-root UID with read-only root filesystem, no added Linux capability, bounded writable temporary areas, `/work` on disk-backed ephemeral storage, and `/data` only where the standalone profile needs persistence.
- Restrict egress by profile and never allow document-controlled network access.
- Produce JSON logs with correlation identifiers and no content, filename, secret, or absolute path.
- Expose queue depth and age, active jobs, step durations, failures, saturation, expiration, retry, and recovery metrics.
- Audit actor, owner, operation, target, and version for every sensitive mutation.
- Build non-UBI engines only from official publisher artifacts. Verify publisher signatures or
  attestations when available and lock every accepted artifact by digest or checksum. Pandoc's
  official release SHA-256 is accepted when its release provides no detached signature.
- Review engine, base-image, operating-system package, font, and transitive-dependency
  vulnerabilities at least weekly. Triage Critical vulnerabilities urgently, rebuild or mitigate
  without waiting for the weekly cycle, and record compatibility regression and rollback evidence.

### 8.1 Local authentication policy

- Do not expose public registration. Inject the initial administrator through startup
  configuration backed by deployment secrets.
- Only administrators create, deactivate, reactivate, or reset local accounts.
- Use Argon2id with configurable parameters defaulting to `m=19456 KiB`, `t=2`, and `p=1`.
- Use opaque CSPRNG session tokens containing at least 128 bits. Store only a one-way token digest
  server-side.
- Default the configurable idle session lifetime to 30 minutes for standard users and 15 minutes
  for administrators, and default the absolute lifetime to 8 hours. Revoke sessions server-side on
  logout, account deactivation, and password reset.
- Allow an administrator to persist one system-wide role-specific idle-session policy. Standard-user
  durations are whole minutes from 5 through 300, inclusive; administrator durations are whole
  minutes from 5 through 60, inclusive. The operator-configured absolute lifetime remains a hard
  ceiling: reject an update if either proposed duration exceeds it. FastAPI enforces the policy for the session user's current effective role on every
  validation, a tightened policy or role change applies to existing unexpired sessions, and a later
  relaxation never revives an expired or revoked session. Policy changes use optimistic concurrency
  and immutable audit records in both storage profiles.
- Send the session token only in a cookie with `HttpOnly`, `Secure`, and `SameSite=Lax`.
- Reject login POST requests carrying a cross-origin `Origin` before evaluating credentials. Allow
  the exact request origin and documented non-browser clients that omit `Origin`.
- Prevent password-reset and account-state races with an atomic account authentication version:
  compare-and-set the verified login snapshot, carry the accepted version in the session, and
  increment it for password resets, deactivation, and reactivation.
- Complete every failed, invalid, or obsolete password-hash path to exactly two current Argon2
  work-profile units without wall-clock sleeps. Count a real current-profile candidate verification
  as one unit and add two dummy units when the legacy or malformed candidate itself is not current
  work.
- Return sanitized stable error envelopes for request validation and expected API failures; never
  reflect submitted passwords or Pydantic validation input.
- T06 uses temporary in-memory account and session adapters behind persistence ports; T12 replaces
  them with profile implementations without changing the authentication service contract.
- An optional strict UTF-8 CSV path provisions users at startup. The complete file is validated
  before one atomic cross-profile upsert; existing normalized usernames receive the supplied
  password and security attributes, their authentication version advances, and sessions are
  revoked. Concurrent PostgreSQL startups serialize this operation.
- Administrators can require password renewal. The user first authenticates with the current
  password, receives a restricted CSRF-protected session, changes and confirms the password, and
  must then log in again with the new password before accessing other workflows.

### 8.2 Retention, malware scanning, and recovery proof

- Retain superseded template versions for 365 days. Cleanup must never delete the active version,
  the ten newest versions of a template, or a version referenced by retained conversion metadata.
  Delete object bytes before fenced metadata acknowledgement so interrupted cleanup is retryable.
- Keep audit records immutable for 365 days, then delete them in bounded transactions that retain
  immutable, content-free cleanup evidence.
- Scan every conversion and template upload after bounded request reading and before validation,
  processing, database reservation, or object persistence. Local ClamAV is the default boundary;
  scanner unavailability, timeout, protocol error, and indeterminate results fail closed. An
  explicit trusted-upstream mode may omit local ClamAV only when a proxy scans every upload before
  forwarding and network policy makes direct or alternate access to the application impossible.
  Selecting that mode is an operator assertion of this external security boundary and must emit a
  startup warning. Infected uploads are rejected by the selected boundary; temporary material is
  securely removed and no durable quarantine is kept. The ClamAV INSTREAM adapter scans directly
  from bounded memory and therefore creates no scanner-side application temporary file.
- The standalone target is RPO 24 hours and RTO 4 hours. The distributed target is RPO 1 hour and
  RTO 2 hours. Exercise each deployed profile at least quarterly with an automated isolated restore
  and readiness check. Retain an immutable report containing backup identity, timestamps, measured
  RPO/RTO, readiness evidence identity, and pass/fail status.
- `markweave backup` and `markweave restore` are production operational front ends, not aliases for
  destructive E2E helpers or the restore-exercise wrapper. In standalone mode they create or
  restore one content-addressed, checksummed set containing an SQLite online snapshot and the
  complete stable object tree; restore requires an offline application, an empty destination, and
  an identity/integrity check before migration and readiness. In distributed mode they orchestrate
  typed, explicitly configured PostgreSQL and S3-provider backup adapters, bind both provider
  recovery-point identities into one signed-or-checksummed manifest, and fail closed when either
  adapter, quiescence proof, identity, or integrity proof is missing. They never execute an
  operator-provided shell string. Restore targets isolated empty database and bucket destinations,
  verifies stable object references before migration/readiness, and never switches production
  traffic. The quarterly exercise consumes these commands and records evidence; test-only S3
  helpers remain forbidden for production recovery.

## 9. Repository and autonomous development

Keep `main` as the only long-lived branch. Every contributor and agent uses a short `<type>/<issue>-<subject>` branch and an isolated worktree when needed. Branch names never identify Codex, another agent, or an automation tool. One pull request normally covers one issue or coherent vertical slice. Draft pull requests run light checks; ready pull requests run the required domain matrix. Squash after required checks, resolved discussions, and an independent agent or GitHub review. Delete the branch and worktree only after verified merge.

The orchestrator selects ready work from the [Linear project](https://linear.app/g1lom/project/markdown-to-docx-and-pdf-converter-3724edb949f9), synchronizes it with `tickets/*.md`, limits scope, reserves components, tracks dependencies, assigns implementation and independent review, serializes or queues merges, watches `main`, and stops merges when `main` is red. Two agents must not edit the same component concurrently.

If `main` fails, stop merges, identify the responsible change, choose an immediate fix or revert pull request, add a regression test, and restore `main` before resuming backlog work.

## 10. Test strategy

Register these Pytest markers:

```toml
[tool.pytest.ini_options]
markers = [
  "unit: deterministic tests without external dependencies",
  "functional: application behavior through public interfaces",
  "integration: integration with a real component",
  "e2e: complete flow against the built container",
  "slow: excluded from explicitly fast checks",
  "requires_pandoc: requires Pandoc",
  "requires_mermaid: requires Mermaid CLI and Chromium",
  "requires_libreoffice: requires LibreOffice",
  "requires_postgres: requires PostgreSQL",
  "requires_s3: requires S3-compatible storage",
]
```

Unit tests remain fast and deterministic and use pytest-mock rather than direct `unittest.mock`. Ruff must enforce that restriction. Calculate branch coverage from unit tests with a blocking 90% threshold and enforce 90% changed-line coverage in pull requests. The frontend independently blocks line, branch, and function coverage below 90% and runs strict TypeScript, lint, formatting, deterministic-build, and OpenAPI-binding freshness checks.

Functional tests exercise assembled application behavior with substituted adapters. Every feature that crosses a real boundary—document engine, database, object store, filesystem boundary, authentication mechanism, worker, or external process—has at least one integration test covering its primary successful path and every relevant failure behavior. Integration tests exercise real engines and both storage contracts. The corpus covers Unicode, headings, tables, footnotes, code, local images, malformed resources, Mermaid, fonts, multiple templates, malicious ZIP/SVG inputs, timeouts, and concurrency. Inspect DOCX as OpenXML and rasterize PDF for golden comparison with controlled tolerances.

Every delivered user-visible or operational workflow has an E2E test against the final rootless image. Cover its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior. Use Playwright, two regular users, and one administrator where applicable. Cover both profiles, ownership, visibility, `202` submission, polling, cancellation, expiration, download, restart recovery, and absence of double execution. Preserve artifacts only on failure. Any integration or E2E exception requires explicit pull-request justification and explicit reviewer approval; cost, inconvenience, or a missing local dependency is not sufficient.

The project manager approved one sequencing exception for T06: its login, session, and local-account
administration flows receive unit, functional ASGI, and real Argon2id/HTTP integration coverage in
T06, while final-image rootless E2E is durable T20/T21 debt. The architectural reason is that T06
deliberately introduces neither the final UBI image nor the standalone/distributed persistence and
deployment boundaries. T20/T21 must repeat the primary flow and relevant authentication,
authorization, revocation, expiration, cookie, and recovery failures against the hardened image;
this is a sequencing exception, not a waiver of final E2E coverage.

The project manager also approved a T12 sequencing exception: T12 must run provider-neutral contract
tests and real PostgreSQL/RustFS integration tests for the primary successful path and every relevant
failure behavior, but its final-image rootless E2E is durable T20/T21 debt because T12 does not yet
deliver the hardened image. T20/T21 must exercise both profiles against that image, including the
relevant storage success, failure, restart, recovery, and concurrency paths. The pull request must
justify this exception and receive explicit reviewer approval; this is not a waiver of integration
coverage or final E2E coverage.

## 11. GitHub Actions

Use selective path/domain detection while keeping one required `CI / gate` result. Support pull requests, `merge_group`, `main`, releases, manual execution, and a scheduled complete suite. Pin actions by commit SHA, minimize permissions, avoid privileged containers and untrusted secret access, cache safely, cancel superseded runs, and apply bounded timeouts.

Light draft checks include formatting, lint, types, unit tests, coverage, and cheap security checks. Ready and merge-queue checks add affected functional, integration, container, and E2E domains. Run the full two-profile matrix on schedule and before releases. Publish the image, SBOM, and provenance only for releases.

Use an isolated release workflow to publish the `markweave` Python distribution with the matching public import `markweave`. A protected pull-request merge to `main` is the sole human gate. A trusted main push that changes `pyproject.toml` must compare `project.version` and the positive integer `tool.markweave.release.attempt` at the exact before and head SHAs. An unchanged version and attempt is a no-op. After an infrastructure failure leaves a run impossible to close or rerun and creates no tag, GitHub Release, PyPI version, or GHCR version tag, a protected recovery pull request may increment the attempt by exactly one to retry the same final version; decreases, skipped attempts, and a non-reset attempt on a new version fail closed. An invalid, non-canonical, pre-release, development, local, epoch, or lower-precedence version also fails closed. A changed canonical spelling with equal PEP 440 precedence remains a valid transition. For a real final-version or protected retry transition, reject an existing `v<version>` tag, matching GitHub Release, or already-published PyPI version. Build the sdist and wheel once from the reviewed main SHA, validate metadata, installation, the documented public import, and artifact integrity, then publish those exact files without rebuilding. Atomically create `v<version>` at that SHA before publishing the matching GitHub Release automatically; a failed-job rerun may reuse partial tag or Release state only after verifying its exact identity. Publish the dynamically tagged GHCR image, provenance, SBOM, and evidence from the same trusted push through a secretless reusable workflow that verifies the Release identity before idempotent evidence attachment. Derive the actual registry digest before remote publication by serializing once into a private local `dir:` transport, validate that digest against the exact staged manifest bytes, and copy only those bytes to GHCR. Authenticated preflight must reject every observed conflicting source-SHA or version tag, same-digest state is idempotent, every copy must be followed by exact digest verification, and workflow/repository concurrency must prevent the automation from racing itself. Because GHCR conditional manifest creation is not established, document the narrow residual race with another principal holding `packages: write`; do not claim immutable or conditional tag creation. Retain a receipt relating the internally bound OCI-archive digest to the registry digest used for provenance. Use PyPI Trusted Publishing through the `pypi` environment with no long-lived token. Grant `contents: write` only to tag/Release creation and evidence attachment, `id-token: write` only to PyPI upload and container attestation, pin actions by full SHA, and prevent pull requests, forks, tag pushes, Release events, and every other untrusted context from publishing. Manual dispatch must never rebuild or republish Python or container artifacts. It may only recover provenance, SBOM, and GitHub Release evidence for an already-published exact Release from a retained artifact produced by a successful upstream build job. Before attestation or attachment, bind the selected run to this upstream repository, workflow file, and the current trusted `main` history by requiring both the release source to be an ancestor of the selected run SHA and that run SHA to be an ancestor of the current workflow SHA; require its successful build job; download the single non-expired bounded artifact by immutable artifact ID; verify its exact regular-file set, checksum manifest, internal OCI identity, publication receipt, version, tag, source SHA, and anonymously readable public GHCR digest; then pass that digest to the existing attestation job and the unchanged evidence files to the existing Release attachment job. Version `0.3.0` maps to the first derived tag `v0.3.0`; future versions are never hardcoded in workflow logic.

The `0.6.0` cutover was the sole skipped-container exception: its exact GitHub tag/Release and PyPI artifact were published from the reviewed source, but its paired container job failed before staging or registry authentication and both `0.6.0` GHCR role tags remained absent. The protected `0.6.0` to `0.6.1` transition verified that state before running an ordinary new paired release; neither manual recovery nor any workflow may rebuild or publish `0.6.0`. After successful `0.6.1` publication, Compose and both quickstarts pin the exact verified backend and frontend registry digests from source `78cb86d450e940a3190591de62ee0ebade216d8b`.

Before the first public release, configure a PyPI pending Trusted Publisher for project `markweave`, owner `Guillaume-Lombardo`, repository `simple-md-to-docx-converter`, workflow `release.yml`, and environment `pypi`. Immediately before every upload, recheck that the exact `markweave` version is unpublished. A pending publisher does not reserve the distribution name. The first successful OIDC upload creates the PyPI project and converts the pending publisher into a normal Trusted Publisher; verify both the project and publisher state after that upload. The public license is Apache-2.0.

## 12. Global acceptance criteria

- Rootless UBI 9/Python 3.14 image works with arbitrary UID and read-only root filesystem.
- DOCX and PDF preserve approved Markdown structures, images, Mermaid, templates, and fonts.
- Both storage profiles pass the same functional contract.
- Queue recovery, leases, idempotency, cancellation, expiration, quotas, and cleanup are deterministic.
- Ownership and administrator permissions hold across API and UI.
- Remote resource loading, unsafe link schemes, unsafe archives, hostile SVG, unsafe subprocess
  input, and sensitive logs are prevented while validated HTTP(S) hyperlinks remain usable.
- Required checks, 90% coverage thresholds, independent review, and English-only repository artifacts are enforced.
- Every applicable real-boundary feature has integration coverage for its primary successful path and every relevant failure behavior, and every delivered user-visible or operational workflow has E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.

## 13. Delivery tickets

| Ticket | Outcome | Depends on |
|---|---|---|
| T00 | Validate UBI 9/Python 3.14, Pandoc, Chromium/Mermaid, LibreOffice, sandboxing, fonts, resource budgets, and rootless runtime through spikes | — |
| T01 | Initialize the English repository, `uv` project, architecture, commands, contribution rules, and local developer workflow | — |
| T02 | Protect `main`, configure required gates, independent review, merge queue or serialized merge, and squash policy | T01 |
| T03 | Implement selective GitHub Actions workflows, `merge_group`, caching, permissions, timeouts, and required gate | T01, T02 |
| T04 | Build the corpus, golden infrastructure, fixtures, marker registration, and deterministic comparison tools | T00, T01 |
| T05 | Configure Ruff, `ty`, Pytest, pytest-cov, pytest-mock restriction, 90% thresholds, and changed-line coverage | T01, T03 |
| T06 | Build FastAPI foundation, configuration, English errors, local accounts, sessions, authorization abstraction, and health endpoints | T01, T05 |
| T07 | Convert approved Markdown to DOCX with fixed Pandoc arguments and reference documents | T04, T06 |
| T08 | Secure ZIP handling, safe extraction, image normalization, hostile SVG handling, and security tests | T04, T07 |
| T09 | Render Mermaid through local Chromium under arbitrary UID, bounded resources, and rootless constraints | T00, T08 |
| T10 | Inventory fonts and licenses; validate OOXML, templates, required styles, substitutions, and canonical conversion | T00, T07 |
| T11 | Produce PDF with isolated LibreOffice profiles, traceability manifest, timeout/cancellation, and golden tests | T09, T10 |
| T12 | Implement repository/object-store abstractions, both profiles, Alembic migrations, atomic files, and contract tests | T05, T06 |
| T13 | Implement persistent queue, job API, state machine, idempotency, embedded/external workers, leases, heartbeat, recovery, and cleanup | T11, T12 |
| T14 | Add immutable ownership, global visibility, search, preferences, fallback template, and cross-profile authorization tests | T06, T12 |
| T15 | Implement versioned template API, downloads, ETag/If-Match, atomic replacement, copy-forward restore, audit, and profile parity | T10, T14 |
| T16 | Build asynchronous conversion UI, template search, progressive polling, cancellation, expiration, and accessible downloads/errors | T13, T14, T15 |
| T17 | Build owner/admin template UI and administrator local-account management with multi-user browser tests | T15, T16 |
| T18 | Add quotas, queue capacity, resource budgets, retention, periodic cleanup, cancellation, and short load tests | T00, T12, T13 |
| T19 | Add structured logs, correlation, metrics, queue observability, audit, version traceability, and cheap readiness | T15, T18 |
| T20 | Build hardened reproducible final image with API, embedded-worker, and external-worker modes, SBOM, scans, and smoke test | T11, T12, T13, T18 |
| T21 | Run rootless E2E for both profiles with three identities, real conversion, restart recovery, concurrency, and failure artifacts | T17, T20 |
| T22 | Finalize selective CI/CD, scheduled full suite, targeted mutation testing, grouped dependency updates, release image, SBOM, provenance, and trusted publication of the verified `markweave` sdist and wheel to PyPI | T03, T21 |
| T23 | Complete English user, template, administrator, API, operations, storage, queue, agent, recovery, and deployment documentation | T22 |
| T24 | Support an explicit trusted-upstream malware-scanning boundary and a ClamAV-free Podman quickstart while preserving fail-closed ClamAV defaults | T23 |
| T25 | Support a CNI-free `slirp4netns` Podman quickstart for trusted-upstream antivirus deployments and publish patch release `0.3.3` | T24 |
| T26 | Preserve strict login-origin validation on custom quickstart ports and same-host reverse proxies, and publish patch release `0.3.4` | T25 |
| T27 | Cache checksum-locked LibreOffice CI artifacts and verify effective quickstart login origins across Compose providers | T22, T26 |
| T28 | Add an explicit loopback-only insecure quickstart for temporary SSH-tunnel testing, fix native same-origin browser login, and publish patch release `0.3.5` | T24, T26, T27 |
| T29 | Allow conversion without a template by using Pandoc's native default reference document while preserving optional immutable-template selection and traceability | T07, T10, T13, T15, T16, T21 |
| T30 | Provision users from a startup CSV, replace existing passwords, require restricted-session password renewal, and publish minor release `0.4.0` | T06, T12, T17, T21 |
| T31 | Build the installed `markweave` CLI foundation, stable output/error contract, and clean-package entry point | T01, T06, T22 |
| T32 | Add secure HTTP login/logout/session/password-renewal commands and owner-only XDG connection profiles without API tokens | T06, T30, T31 |
| T33 | Add HTTP-only conversion submission and complete user-owned job lifecycle commands | T13, T29, T32 |
| T34 | Add HTTP-only template, version, preference, and fallback administration commands | T15, T32 |
| T35 | Add HTTP-only user administration, audit, health, readiness, and metrics commands | T17, T19, T30, T32 |
| T36 | Add supported `serve`, `worker`, `doctor`, and `migrate` operational commands | T12, T20, T31, T39 |
| T37 | Add guarded standalone and distributed `backup` and `restore` operational commands | T12, T18, T20, T31, T39 |
| T38 | Use the supported `markweave` commands as every source-built final-container, deployment/recovery, smoke, and E2E entry point | T20, T21, T33, T34, T35, T36, T37, T40 |
| T39 | Make `MARKWEAVE_*` canonical while preserving fail-closed `MD_CONVERTER_*` compatibility throughout 0.x | T06, T20, T26 |
| T40 | Clarify the supported Python API, split optional backend extras, and complete PyPI metadata and installation verification | T22, T31, T39 |
| T41 | Decompose the FastAPI application into routers, schemas, dependencies, error handling, lifecycle, and a small composition root | T06, T44 |
| T42 | Decompose worker claim, heartbeat, execution, publication, cancellation, recovery, and cleanup orchestration against the finalized persistence ports | T13, T21, T43 |
| T43 | Decompose job and template persistence by responsibility while preserving cross-profile contracts and transactional invariants | T12, T15, T44 |
| T44 | Eliminate unclosed database/component resources and enforce targeted `ResourceWarning` regressions | T06, T12 |
| T45 | Commit and validate a deterministic OpenAPI artifact and block accidental incompatible HTTP changes | T06, T41 |
| T46 | Add security reporting, supported-version, disclosure, and operational-support policies | T22, T23, T40 |
| T47 | Add a user-facing changelog and deterministic package/container/database upgrade and rollback guidance | T22, T23, T39 |
| T48 | Expand bounded mutation testing across authentication, input security, queue, worker, retention, and storage invariants | T05, T22, T41, T42, T43 |
| T49 | Remove residual retired-package artifacts and enforce clean `markweave` namespace and release outputs | T22, T40 |
| T50 | Run the complete package, CLI, container, configuration, contract, documentation, and maintainability acceptance matrix | T38, T41, T42, T43, T44, T45, T46, T47, T48, T49 |
| T54 | Publish the post-T38 CLI-entrypoint release and atomically pin public Compose and quickstarts to its immutable image | T22, T38, T40 |
| T55 | Publish LibreOffice descendant PID probes atomically in the real-process cancellation test harness | T21 |
| T56 | Allow safe HTTP(S) hyperlinks without remote resource loading and verify the workflow against the final image | T07, T08, T21 |
| T57 | Preserve the uploaded filename stem for DOCX, PDF, and combined conversion downloads | T16, T21 |
| T58 | Define the Next.js frontend migration architecture, same-origin API boundary, staged cutover, runtime topology, and rollback contract | T20, T21, T45 |
| T59 | Persist and audit an administrator-controlled idle-session policy enforced by FastAPI across both storage profiles | T06, T12, T19, T45, T58 |
| T60 | Build the `web/` Next.js, TypeScript, and Tailwind CSS foundation, typed API transport, frontend quality gates, and rootless smoke test | T45, T58 |
| T61 | Migrate login, logout, password renewal, protected navigation, and session-expiry behavior to Next.js | T30, T59, T60 |
| T62 | Migrate the asynchronous conversion workflow to Next.js with behavioral, accessibility, and failure parity | T16, T57, T61, T65 |
| T63 | Migrate template, user, and idle-session-policy administration to Next.js | T17, T59, T61, T65, T66 |
| T64 | Cut over browser routing, remove the legacy frontend, harden and publish the frontend runtime, and complete two-profile rootless E2E acceptance | T20, T21, T22, T62, T63 |
| T65 | Expose authenticated domain-specific conversion options, template context, and the administrator absolute-session ceiling for authoritative frontend runtime metadata | T45, T57, T59, T61 |
| T66 | Expose authoritative role-specific idle-session policy bounds, defaults, and minute granularity for the administration frontend | T59, T65 |
| T68 | Restore host routing for the CNI-free rootless Podman trusted-upstream and insecure Next.js quickstarts | T64 |
| T67 | Migrate root browser-test and Next.js tooling to one deterministic pnpm workspace while preserving the isolated npm Mermaid graph, release evidence, and rollback | T64 |
| T69 | Validate and specify the pinned local anydoc engine, supported formats, asset-aware serialization, PDF limitations, supply chain, and resource contract | T04, T20, T45, T64 |
| T70 | Implement the external Podman/Kubernetes isolation broker and authenticated bounded protocol, disposable anydoc attempt runner, bounded internal renderer adapter, and deterministic asset-aware Markdown package builder | T08, T18, T20, T69 |
| T71 | Add authenticated persistent reverse-conversion jobs, API, workers, observability, and both storage profiles | T13, T19, T45, T70 |
| T72 | Build the experimental Next.js Revert workspace with accessible stamped navigation and complete asynchronous job behavior | T60, T61, T64, T67, T71 |
| T73 | Harden, document, and verify reverse conversion against exact final images and both storage profiles | T21, T22, T23, T46, T48, T50, T67, T70, T71, T72 |

Recommended delivery order: T00 and T01 can start in parallel, and T00 may continue alongside only foundation work that does not depend on its unresolved outcomes. T04 still waits for both T00 and T01. Continue with the remaining autonomous foundation (T02–T05), document conversion (T06–T11), storage/queue/ownership (T12–T15), Web product (T16–T17), then industrialization (T18–T23), followed by the trusted-upstream deployment option, its rootless compatibility correction, the public-origin correction, the CI/origin reliability follow-up, the bounded SSH-tunnel evaluation mode, optional-template conversion, and startup user provisioning with required password renewal (T24–T30). For the frontend migration, complete T58 first; T59 and T60 may then proceed independently, followed by T61, the authoritative runtime-metadata prerequisite T65, and the authoritative session-policy-bounds prerequisite T66 before the parallel workflow migrations T62 and T63 and the single verified cutover T64.

T68 follows T64 as the focused correction for host forwarding into the shared rootless Podman
network namespace. It does not change the router's public-host policy or the host's loopback-only
publication boundary.

For experimental reverse conversion, T69 fixes the approved evidence and contract. T70 implements
the trusted external isolation broker and its Podman/Kubernetes backends, authenticated bounded
protocol, disposable attempt runner, bounded internal renderer adapter, and package builder. T71
binds that runner and its content-free stable-unit termination proof to persistent leases, recovery,
publication, and the backend workflow. T67 follows T64 and establishes the normative
package-manager, bootstrap, workspace, command, and lockfile contract before T72 starts. T72 then
builds the Revert workspace on that finalized pnpm toolchain. T46, T48, and T50 must finish
their baseline security/support policies, mutation gate, and cross-surface documentation/acceptance
ownership before T73 begins. T73 may then add only reverse-specific extensions to those established
surfaces; it does not reopen their baseline scope or edit an exclusively owned path while another
ticket is active. T73 owns the complete final-image, two-profile, cross-format, reverse-security,
dedicated reverse-documentation, and release-readiness acceptance matrix. OCR remains outside this
sequence.

For T31–T50, begin T31, T39, and T44 in parallel because their owned paths do not overlap. T32
follows T31. T33, T34, and T35 then run in parallel by filling the command-family modules and test
fixtures pre-registered by T31; they never edit the root registry or shared help snapshots. T36 and
T37 may run in parallel after T31 and T39 because they fill separate pre-registered runtime- and
recovery-operations families. T40 follows the CLI and configuration foundations and is
the sole owner of `pyproject.toml`, package extras/metadata, and release-install verification after
T31's initial entry-point change. T41 and T43 follow T44 and run independently. T42 follows T43 so
the worker decomposition consumes finalized ports without concurrent port edits. T45 follows T41;
T46 and T49 follow T40 and run in parallel on dedicated policy files and namespace-cleanliness
checks without editing distribution metadata or release-install verification. T47 follows T39 and
owns only its dedicated changelog, upgrade guide, and changelog-check files. T48 waits for the
refactored mutation targets. T38 integrates the finished CLI into container assets and executable
deployment/recovery tests without editing shared documentation navigation. T54 follows T38, T40,
and T22 to publish and pin the public Compose and quickstart migration, before T50 performs final
cross-surface acceptance and exclusively owns README, `docs/index.md`, and cross-guide navigation
updates. Each ticket lists its exclusive files or components; a worker must stop and resynchronize
the ticket before touching any path owned by another active ticket.

## 14. Deferred decisions and initial-scope exclusions

- Exact UBI 9 Python 3.14 image digest and availability of Chromium, LibreOffice, and Pandoc from approved build sources.
- Validate the approved Chromium sandbox strategy on k3s after the rootless Podman proof. OpenShift
  validation and the final target security context remain deferred and are required before claiming
  OpenShift compatibility.
- T10 owns the exact official font artifacts, versions, notices, Fontconfig substitution details,
  and the scripts that explicitly require Noto coverage. The approved font families in section 2
  remain fixed while these implementation details are deferred.
- T18 owns maximum upload and decompressed sizes; file, image, and diagram counts; active-job and
  queue-depth limits; worker duration, memory, and ephemeral storage; source/result retention;
  quotas; and cleanup schedules. Keep workload-dependent production values configurable. The
  approved template/audit retention, ClamAV, and recovery contracts are fixed in section 8.2.
- T22 fixes the complete-suite schedule to Sunday at 03:17 UTC, the heavy-job timeout to 45 minutes,
  and heavy-matrix parallelism to two jobs. Monitor hosted usage before changing this policy.
- PDF/A output and automatic Word or PDF table-of-contents generation are outside the initial
  product scope. Adding either capability requires separately approved future scope.
- T69 owns the exact reverse-conversion format matrix, pinned anydoc release and binding, asset-aware
  serializer strategy, content-free manifest schema, asset-free download type, honest text-PDF/image
  contract, and synchronous-native-call cancellation/timeout/memory/lease decision. Its approved
  contract uses a trusted external broker as the sole holder of Podman/Kubernetes workload
  authority, a narrow authenticated Unix/mTLS control protocol, one immutable-image/fixed-argument
  disposable kernel-isolated unit per attempt, configurable T71-owned CPU/memory/PID/workspace
  budgets and autonomous runtime deadline, mandatory crash-consistent content-free inventory and
  termination tombstones, fail-closed startup/reconnect orphan reconciliation,
  termination proof before recovery, and the bounded internal adapter; no implementation
  may expose a raw runtime socket or workload-mutating service account to the application, broaden
  the validated format/PDF claims, or fall back to shared-process execution, a second parser, or an
  unconstrained serializer fork.
- T70 owns the broker service and protocol, Podman/Kubernetes isolation backends, immutable
  attempt-image/argument policy, mandatory crash-consistent managed-unit inventory/tombstones,
  supplementary runtime-label discovery, startup/reconnect orphan sweep, stable-unit
  terminate-and-prove protocol, and
  deterministic canonical generation of the content-free traceability manifest, including stable
  field and archive-entry ordering, together with the bounded renderer compatibility boundary and
  Markdown/assets package builder. T71 may configure reviewed budgets and persist the stable unit
  identity/proof and resulting package, but must not introduce a second broker, runner, parser,
  renderer adapter, or manifest serializer.
- T71 owns configurable reverse-upload, result, asset, concurrency, duration, queue, and retention
  limits. It must derive them from measured T69 evidence and must not silently copy forward-
  conversion values.
- T69 also owns the measured CPU-time, wall-time, peak-memory, thread-count, and concurrency
  evidence used to define the low-compute operating envelope. T71 keeps those production budgets
  configurable and prevents reverse jobs from starving forward conversions.
- OCR and Firecrawl's hosted Parse fallback are excluded from T69-T73. Adding either requires a
  separate product, privacy, security, cost, egress, retention, and operations decision.

Do not silently resolve deferred parameters in unrelated implementation work.
