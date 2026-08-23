# Markdown to DOCX and PDF Converter

**Status:** Functional, technical, and autonomous-development specification

**Date:** August 23, 2026

**Runtime target:** UBI 9 container, Python 3.14, rootless Podman, and OpenShift

**Forge and CI:** GitHub, GitHub Actions, and trunk-based development

## 1. Objective

Build a Web service that converts Markdown documents to DOCX, PDF, or both through a browser interface and a documented HTTP API.

The service accepts a standalone `.md` file or a `.zip` archive containing Markdown and local resources. Users select an administrable Word template. The service resolves and normalizes local images, renders Mermaid diagrams locally, applies template styles, and retains source and result files only for the configured asynchronous-processing and download period.

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
| Git workflow | Trunk-based, short branches, pull requests to `main`, squash merge |
| Python dependencies | `uv` |
| Quality tools | Ruff, `ty`, Pytest, pytest-cov, and pytest-mock |
| Coverage | At least 90% of application Python and 90% of changed Python lines |
| Standalone storage | SQLite and files on a PVC, one replica |
| Distributed storage | PostgreSQL and S3-compatible object storage |
| Initial authentication | Local username/password behind an OIDC-ready abstraction |
| Document resources | Remote resources are forbidden |
| Repository language | English for code, identifiers, docstrings, UI, errors, logs, documentation, commits, and pull requests |

Asynchronous processing avoids coupling job duration to browser, OpenShift Route, and application request timeouts. It provides bounded concurrency, restart recovery, state tracking, and one contract for both storage profiles. No extra broker is used: SQLite carries the standalone queue and PostgreSQL carries the distributed queue.

## 3. Functional requirements

### 3.1 Conversion jobs

- Accept `.md` and `.zip` uploads.
- Select the main Markdown file deterministically.
- Validate an archive before extraction and resolve only safe relative resources.
- Support the approved Pandoc-compatible headings, lists, tables, links, footnotes, quotes, code blocks, image attributes, and metadata.
- Render every Mermaid block locally.
- Select an active template and immutable template version.
- Produce DOCX, PDF, or a ZIP containing both.
- Return a job identifier immediately and expose state, current step, progress, safe errors, cancellation, result download, and expiration.
- Use states `queued`, `running`, `succeeded`, `failed`, `cancelled`, and `expired`.
- Return the component and template versions needed to reproduce a conversion.

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

- **Convert:** upload or drag-and-drop, search and select a template, choose output, create a job, poll with progressive backoff, cancel, inspect status, download, and display accessible English errors.
- **Templates:** list visible templates and owners, filter “my templates,” create, download, rename, replace, restore, delete, and choose the preferred template.

Administrators also receive a users tab to create, search, activate, deactivate, and reset local accounts. Every permission is enforced server-side. Any application JavaScript receives its own tests and coverage checks.

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

Fix the Markdown dialect and extension list in product configuration. Prefer `commonmark_x` with explicitly approved table, footnote, image-attribute, and metadata extensions. Disable `raw_html`, `raw_tex`, user filters, user includes, and remote resources.

Evaluate `pandoc --sandbox` in T00 with images and `reference.docx`; do not claim it is enabled until the complete pipeline passes. Run Pandoc from the job workspace with fixed arguments, no shell, no network, deadlines, memory limits, and an unprivileged identity.

Render Mermaid through a locally installed Chromium; Puppeteer must never download a browser during build or runtime. Bound pixel resolution and physical document width/height while preserving aspect ratio. Chromium must work with arbitrary UID, writable HOME/XDG directories, bounded `/dev/shm`, read-only root filesystem, and no network. Do not assume `--no-sandbox`; T00 must validate the OpenShift security context and record an explicit security decision.

Generate DOCX with the selected reference document. Generate PDF from DOCX to preserve Word styling. Give every LibreOffice invocation an isolated temporary user profile and terminate its whole process group on timeout or cancellation.

Templates declare expected fonts. Validate fonts, licenses, Fontconfig behavior, required Pandoc styles, macros, external OOXML relationships, blank canonical conversion, and LibreOffice opening before activation.

## 6. Storage and job execution

### 6.1 Standalone profile

Use SQLite plus atomic files under `/data`, one application replica, and an embedded bounded worker. Persist jobs before acknowledging upload. Recover expired leases and unfinished jobs after restart. Never share SQLite between pods.

### 6.2 Distributed profile

Use PostgreSQL for metadata, queue state, leases, heartbeat, and concurrency control; use S3-compatible object storage for uploads, results, and template versions. Workers may run separately. Claim jobs with transactional locking such as `FOR UPDATE SKIP LOCKED`. Prevent simultaneous duplicate execution.

### 6.3 Shared abstractions

Expose repository and object-store interfaces with the same contract tests for both implementations. Select exactly one coherent profile at startup and fail fast on mixed or incomplete configuration. Manage schemas with Alembic. Document backup and restoration for both profiles.

Persist users, templates, template versions, preferences, conversion jobs, job events or attempts, and audit records. Object keys and paths derive only from stable identifiers, never visible names.

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

## 9. Repository and autonomous development

Keep `main` as the only long-lived branch. Every contributor and agent uses a short `<type>/<issue>-<subject>` branch and an isolated worktree when needed. Branch names never identify Codex, another agent, or an automation tool. One pull request normally covers one issue or coherent vertical slice. Draft pull requests run light checks; ready pull requests run the required domain matrix. Squash after required checks, resolved discussions, and an independent agent or GitHub review. Delete the branch and worktree only after verified merge.

The orchestrator selects ready work, limits scope, reserves components, tracks dependencies, assigns implementation and independent review, serializes or queues merges, watches `main`, and stops merges when `main` is red. Two agents must not edit the same component concurrently.

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

Functional tests exercise assembled application behavior with substituted adapters. Integration tests exercise real engines and both storage contracts. The corpus covers Unicode, headings, tables, footnotes, code, local images, malformed resources, Mermaid, fonts, multiple templates, malicious ZIP/SVG inputs, timeouts, and concurrency. Inspect DOCX as OpenXML and rasterize PDF for golden comparison with controlled tolerances.

E2E tests use the final rootless image, Playwright, two regular users, and one administrator. Cover both profiles, ownership, visibility, `202` submission, polling, cancellation, expiration, download, restart recovery, and absence of double execution. Preserve artifacts only on failure.

## 11. GitHub Actions

Use selective path/domain detection while keeping one required `CI / gate` result. Support pull requests, `merge_group`, `main`, releases, manual execution, and a scheduled complete suite. Pin actions by commit SHA, minimize permissions, avoid privileged containers and untrusted secret access, cache safely, cancel superseded runs, and apply bounded timeouts.

Light draft checks include formatting, lint, types, unit tests, coverage, and cheap security checks. Ready and merge-queue checks add affected functional, integration, container, and E2E domains. Run the full two-profile matrix on schedule and before releases. Publish the image, SBOM, and provenance only for releases.

## 12. Global acceptance criteria

- Rootless UBI 9/Python 3.14 image works with arbitrary UID and read-only root filesystem.
- DOCX and PDF preserve approved Markdown structures, images, Mermaid, templates, and fonts.
- Both storage profiles pass the same functional contract.
- Queue recovery, leases, idempotency, cancellation, expiration, quotas, and cleanup are deterministic.
- Ownership and administrator permissions hold across API and UI.
- Remote resources, unsafe archives, hostile SVG, unsafe subprocess input, and sensitive logs are prevented.
- Required checks, 90% coverage thresholds, independent review, and English-only repository artifacts are enforced.

## 13. Delivery tickets

| Ticket | Outcome | Depends on |
|---|---|---|
| T00 | Validate UBI 9/Python 3.14, Pandoc, Chromium/Mermaid, LibreOffice, sandboxing, fonts, resource budgets, and rootless runtime through spikes | — |
| T01 | Initialize the English repository, `uv` project, architecture, commands, contribution rules, and local developer workflow | T00 |
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
| T22 | Finalize selective CI/CD, scheduled full suite, targeted mutation testing, grouped dependency updates, release image, SBOM, and provenance | T03, T21 |
| T23 | Complete English user, template, administrator, API, operations, storage, queue, agent, recovery, and deployment documentation | T22 |

Recommended delivery order: risk spikes and autonomous foundation (T00–T05), document conversion (T06–T11), storage/queue/ownership (T12–T15), Web product (T16–T17), then industrialization (T18–T23). Stabilize contracts and ownership boundaries before parallel work.

## 14. Parameters to determine during T00 and T04

- Exact UBI 9 Python 3.14 image digest and availability of Chromium, LibreOffice, and Pandoc from approved build sources.
- Exact CommonMark dialect/extensions and feasibility of `pandoc --sandbox` with images.
- Chromium sandbox strategy and OpenShift security context.
- Approved fonts, licenses, Fontconfig behavior, and substitution policy.
- Maximum upload/decompressed size, files, images, diagrams, active jobs, queue depth, worker duration, memory, and ephemeral storage.
- Source/result retention, template-version retention, audit retention, antivirus integration, and cleanup schedule.
- Argon2id parameters and local-account policy.
- GitHub Actions heavy-job timeouts, full-suite frequency, and usage budget.
- S3 implementation used by E2E.
- PDF/A and Word/PDF table-of-contents support.

Do not silently resolve these parameters in implementation work.
