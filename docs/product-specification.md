# Markdown to DOCX and PDF Converter

**Status:** Functional, technical, and autonomous-development specification

**Date:** August 26, 2026

**Runtime target:** UBI 9 container, Python 3.14, rootless Podman, and OpenShift

**Forge and CI:** GitHub, GitHub Actions, and trunk-based development

## 1. Objective

Build a Web service that converts Markdown documents to DOCX, PDF, or both through a browser interface and a documented HTTP API.

The service accepts a standalone `.md` file or a `.zip` archive containing Markdown and local resources. Users may select an administrable Word template or use Pandoc's native default reference document. The service resolves and normalizes local images, renders Mermaid diagrams locally, applies the selected document-style mode, and retains source and result files only for the configured asynchronous-processing and download period.

The product includes a conversion page, template administration, local authentication, two configurable storage profiles, a hardened UBI 9 image, selective GitHub Actions workflows, and an autonomous Codex development workflow.

## 2. Fixed decisions

| Topic | Decision |
|---|---|
| Application language | Python 3.14 |
| Base image | Official `ubi9/python-314`, pinned by digest in CI |
| API and Web | FastAPI, server-rendered HTML, limited native JavaScript |
| Processing | Asynchronous jobs with a persistent queue and status API |
| Markdown to DOCX | Pandoc |
| Mermaid | Local Mermaid CLI and Chromium |
| DOCX to PDF | LibreOffice headless |
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
| Document resources | Remote resources are forbidden |
| Markdown reader | `commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html`, with raw HTML rejected before Pandoc |
| Browser sandbox | Keep Chromium's sandbox enabled; validate a minimal seccomp/user-namespace profile on rootless Podman, then k3s; never use `--no-sandbox`; defer OpenShift proof |
| Document fonts | Liberation plus Carlito/Caladea, DejaVu as fallback, and Noto only for explicitly required scripts |
| Distributed test object store | RustFS for CI and k3s, behind a provider-neutral AWS S3-compatible contract |
| Repository language | English for code, identifiers, docstrings, UI, errors, logs, documentation, commits, and pull requests |
| Superseded template retention | 365 days; never delete the active version or ten newest versions per template |
| Audit retention | Immutable for 365 days, then bounded traceable deletion |
| Upload malware scanning | Local ClamAV before processing or durable persistence by default; an explicit trusted-upstream mode is permitted only behind a non-bypassable proxy that scans every upload before forwarding; the explicit insecure evaluation exception may omit scanning only while loopback-bound behind a temporary SSH tunnel; fail closed in ClamAV mode; no durable quarantine |
| Recovery targets | Standalone RPO 24h/RTO 4h; distributed RPO 1h/RTO 2h; automated quarterly proof |

Asynchronous processing avoids coupling job duration to browser, OpenShift Route, and application request timeouts. It provides bounded concurrency, restart recovery, state tracking, and one contract for both storage profiles. No extra broker is used: SQLite carries the standalone queue and PostgreSQL carries the distributed queue.

## 3. Functional requirements

### 3.1 Conversion jobs

- Accept `.md` and `.zip` uploads.
- Select the main Markdown file deterministically.
- Validate an archive before extraction and resolve only safe relative resources.
- Support the approved Pandoc-compatible headings, lists, tables, links, footnotes, quotes, code blocks, image attributes, and metadata.
- Render every Mermaid block locally.
- Use either Pandoc's native default reference document or an active immutable template version.
- Produce DOCX, PDF, or a ZIP containing both.
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

Provide a login page and two main server-rendered pages:

- **Convert:** upload or drag-and-drop, choose Pandoc's default or search and select a template,
  choose output, create a job, poll with progressive backoff, cancel, inspect status, download, and
  display accessible English errors.
- **Templates:** list visible templates and owners, filter “my templates,” create, download, rename, replace, restore, delete, and choose the preferred template.

Administrators also receive a users tab to create, search, activate, deactivate, and reset local accounts. Every permission is enforced server-side. Any application JavaScript receives its own tests and coverage checks.

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

## 4. Input contract

A standalone Markdown file is accepted only when it has no local-resource dependency. The service never downloads document images, stylesheets, or other remote resources.

Recommended archive layout:

```text
document.zip
├── document.md
└── assets/
    ├── architecture.png
    ├── diagram.svg
    └── screenshot.jpg
```

Select root `document.md` first, otherwise the sole `.md` file, and reject ambiguity. Reject absolute and escaping paths, symlinks, encrypted archives, ZIP bombs, abnormal compression ratios, configured-limit violations, disallowed types, and every remote URL including internal-network destinations.

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
- active-template search and listing;
- template creation, metadata update, current/previous content download, replacement, version listing, copy-forward restoration, deletion/archive, and per-user default selection;
- `/health/live`, `/health/ready`, metrics, and `/docs`.

Support `Idempotency-Key` for job creation. Enforce owner/administrator access to source, state, cancellation, and result. Return stable functional error codes without traces or local paths. Keep readiness cheap; it must not run a conversion.

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
- Default the configurable idle session lifetime to 30 minutes and the absolute lifetime to 8
  hours. Revoke sessions server-side on logout, account deactivation, and password reset.
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

Unit tests remain fast and deterministic and use pytest-mock rather than direct `unittest.mock`. Ruff must enforce that restriction. Calculate branch coverage from unit tests with a blocking 90% threshold and enforce 90% changed-line coverage in pull requests.

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

Before the first public release, configure a PyPI pending Trusted Publisher for project `markweave`, owner `Guillaume-Lombardo`, repository `simple-md-to-docx-converter`, workflow `release.yml`, and environment `pypi`. Immediately before every upload, recheck that the exact `markweave` version is unpublished. A pending publisher does not reserve the distribution name. The first successful OIDC upload creates the PyPI project and converts the pending publisher into a normal Trusted Publisher; verify both the project and publisher state after that upload. The public license is Apache-2.0.

## 12. Global acceptance criteria

- Rootless UBI 9/Python 3.14 image works with arbitrary UID and read-only root filesystem.
- DOCX and PDF preserve approved Markdown structures, images, Mermaid, templates, and fonts.
- Both storage profiles pass the same functional contract.
- Queue recovery, leases, idempotency, cancellation, expiration, quotas, and cleanup are deterministic.
- Ownership and administrator permissions hold across API and UI.
- Remote resources, unsafe archives, hostile SVG, unsafe subprocess input, and sensitive logs are prevented.
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
| T38 | Use the supported `markweave` commands as every final-container, Compose, quickstart, and E2E entry point | T20, T21, T33, T34, T35, T36, T37, T40 |
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

Recommended delivery order: T00 and T01 can start in parallel, and T00 may continue alongside only foundation work that does not depend on its unresolved outcomes. T04 still waits for both T00 and T01. Continue with the remaining autonomous foundation (T02–T05), document conversion (T06–T11), storage/queue/ownership (T12–T15), Web product (T16–T17), then industrialization (T18–T23), followed by the trusted-upstream deployment option, its rootless compatibility correction, the public-origin correction, the CI/origin reliability follow-up, the bounded SSH-tunnel evaluation mode, optional-template conversion, and startup user provisioning with required password renewal (T24–T30).

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
deployment/recovery tests without editing shared documentation navigation. T50 performs final
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

Do not silently resolve deferred parameters in unrelated implementation work.
