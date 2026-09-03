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
backend/frontend images in both storage profiles.

## Acceptance criteria

* Build the exact final backend and frontend images once and run the complete authenticated Revert
  workflow with Playwright against standalone SQLite/filesystem and distributed PostgreSQL/S3
  profiles.
* Cover at least one representative file from every format family approved by T69, including
  structure and deterministic ZIP/Markdown/assets inspection rather than download success alone.
* Verify owner isolation, scanner rejection, unsupported/encrypted/malformed input, resource
  limits, capacity responses, idempotency, cancellation, expiration, restart recovery, lease
  recovery, concurrency, absence of double execution, backend/frontend outages, and scanned/image-
  only PDF `needs_ocr` behavior with no network fallback.
* Prove the final backend runs anydoc under arbitrary UID, read-only root, no added capabilities,
  bounded `/work`, and the approved no-document-egress policy; retain bounded failure artifacts only
  on failure.
* Exercise result integrity: safe root Markdown filename, safe `assets/` paths, exact relative image
  references, media signatures/extensions, no orphaned assets, deterministic ordering/digest,
  private download headers, and traceability manifest.
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
* T70
* T71
* T72

## Implementation boundary

* Own final-image integration, two-profile browser/API acceptance, selective CI wiring, cross-
  cutting documentation, and release-readiness evidence.
* Do not expand the approved format matrix or add OCR.
* Do not publish a release or change a public deployment digest without a separately approved
  release ticket.

## Quality requirements

* Preserve the matched backend/frontend release identity and existing production routing/security
  contracts.
* Require independent review and exact-head plus exact-main CI evidence before completion.
* Keep repository artifacts and user-facing text in English.

## Progress

* 2026-09-03: Created from the approved feasibility decomposition; blocked by T70, T71, and T72.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
