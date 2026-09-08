---
ticket: T73
linear_id: G1L-541
linear_url: https://linear.app/g1lom/issue/G1L-541/t73-verify-and-document-reverse-conversion-end-to-end
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T73 - Verify and document reverse conversion end to end

## Objective

Harden, document, and verify the complete reverse-conversion workflow against the exact final
backend, frontend, and reverse-attempt images in both storage profiles.

## Acceptance criteria

* Extend the atomic release set with the third public reverse-attempt image, pinned by immutable
  digest and built from the same reviewed source identity as the matching backend and frontend
  images. Build the exact final three-image set once and run the complete authenticated Revert
  workflow with Playwright against standalone SQLite/filesystem and distributed PostgreSQL/S3
  profiles.
* From the exact same final backend image, run the installed HTTP-only `markweave` reverse-job CLI
  end to end against both storage profiles. Verify human and JSON output, stable exit codes, login
  profile isolation, submission, listing, status/polling, cancellation, and result download with two
  regular users and one administrator. Cover the primary successful lifecycle plus non-enumerating
  cross-owner/admin access, scanner rejection, unsupported or `needs_ocr` input, capacity failure,
  cancellation before publication, expiration, and backend-unavailable behavior; inspect the
  downloaded Markdown/package rather than accepting command success alone.
* Cover at least one representative file from every format family approved by T69, including
  structure and deterministic ZIP/Markdown/assets inspection rather than download success alone.
* Validate the versioned `GET /api/v1/reversions/capabilities` schema, OpenAPI representation,
  authenticated private/no-store contract, deterministic parity across both storage profiles, and
  exact agreement with configured server admission. Prove in browser E2E that the visible hint,
  file chooser, and client preflight derive from the response without a duplicate format matrix,
  update when configured constraints change, and disable submission safely for unavailable or
  unsupported-version capability responses.
* Verify owner isolation, scanner rejection, unsupported/encrypted/malformed input, resource
  limits, capacity responses, idempotency, cancellation, expiration, restart recovery, lease
  recovery, concurrency, absence of double execution, backend/frontend outages, and scanned/image-
  only PDF `needs_ocr` behavior with no network fallback.
* Prove the final backend runs anydoc under arbitrary UID, read-only root, no added capabilities,
  bounded `/work`, and the approved no-document-egress policy; retain bounded failure artifacts only
  on failure.
* Prove the exact final image requests no GPU or accelerator resource, loads no ML runtime, and
  starts no browser, Pandoc, LibreOffice, or other document-engine subprocess during reverse jobs.
  Record CPU time, wall time, peak memory, threads, and concurrency behavior against the approved
  T69 low-compute envelope.
* Exercise result integrity: safe root Markdown filename, safe `assets/` paths, exact relative image
  references, decoded media signatures/extensions, rejection of non-image/mismatched/polyglot and
  animated/multi-frame assets, hostile-SVG sanitization and network isolation, no orphaned assets,
  deterministic ordering/digest, private download headers, and the T70-generated content-free
  traceability manifest.
* Prove with two regular users and one administrator that reverse source, status, cancellation, and
  result routes are owner-only and non-enumerating. Verify that administrator operational metadata
  is separately authorized, audited, content-free, and cannot reveal filenames, Markdown, assets,
  content-derived digests, or a download capability.
* Extend selective and scheduled CI domains, SBOM/vulnerability/license evidence, mutation scope
  where risk-ranked, and release-install verification for the native anydoc dependency.
* Complete user, API, operations, security, configuration, supported-format, limitation,
  troubleshooting, backup/restore, and upgrade/rollback documentation. State explicitly that OCR
  and hosted Firecrawl fallback are absent.
* Run every applicable canonical Python, frontend, contract, container, integration, E2E, and
  documentation check; no integration or E2E waiver is allowed for this delivered user-visible
  workflow.

## Dependencies

* T21
* T22
* T23
* T46
* T48
* T50
* T67
* T70
* T71
* T72

## Implementation boundary

* Own final-image integration, two-profile browser/API/CLI acceptance, selective CI wiring, cross-
  cutting documentation, and release-readiness evidence.
* Begin only after T46, T48, T50, and T67 complete their baseline policy, mutation, documentation,
  acceptance, and JavaScript-tooling ownership. Add narrowly scoped reverse-conversion extensions
  to the established surfaces without reopening those tickets' baseline decisions.
* Do not edit `SECURITY.md`, `SUPPORT.md`, README, `docs/index.md`, shared cross-guide navigation,
  T67's finalized package-manager/bootstrap/workspace contract, or T48's baseline mutation runner/
  gate unless ownership is explicitly transferred. Put reverse documentation in dedicated guides,
  use the toolchain made normative by T67, and extend only the approved reverse mutation targets
  through T48's established extension mechanism. T73 does not independently select pnpm, Corepack,
  or another package manager.
* Do not expand the approved format matrix or add OCR.
* Do not publish a release or change a public deployment digest without a separately approved
  release ticket.

## Quality requirements

* Preserve the matched backend/frontend/reverse-attempt release identity and existing production
  routing/security contracts.
* Treat regression beyond the approved CPU, memory, thread, or concurrency envelope as blocking.
* Require independent review and exact-head plus exact-main CI evidence before completion.
* Keep repository artifacts and user-facing text in English.

## Progress

* 2026-09-03: Created from the approved feasibility decomposition; blocked by T70, T71, and T72.
* 2026-09-03: Final-image acceptance now includes explicit CPU-only proof and measured low-compute
  regression gates.
* 2026-09-05: Product-manager decision deferred the third public reverse-attempt image from T70 to
  T73. T73 now owns its atomic inclusion in the final matched release set; T70 and T71 may use only
  locally built or CI-built exact images before that publication boundary.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
