---
ticket: T72
linear_id: G1L-540
linear_url: https://linear.app/g1lom/issue/G1L-540/t72-build-the-experimental-revert-workspace
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T72 - Build the experimental Revert workspace

## Objective

Add an experimental Next.js `Revert` workspace for submitting document-to-Markdown jobs and
downloading their results.

## Acceptance criteria

* Add `Revert` to the authenticated application navigation with a clearly visible stamp-style
  `Experimental` treatment associated with the tab label; the state is also conveyed in accessible
  text and never by shape, position, or color alone.
* Build an accessible `/revert` workspace with file selection and drag-and-drop, submission,
  progressive polling/backoff, status, cancellation, expiration, safe errors, and result download.
  Fetch authenticated `GET /api/v1/reversions/capabilities` and derive the supported-format hint,
  file chooser, and bounded client validation from its versioned response. Do not duplicate or
  hardcode the T69 format matrix or fallback constraints; if capabilities are unavailable or an
  unsupported schema version is returned, disable submission and render the safe backend-
  unavailable state. Server validation remains authoritative.
* Call the FastAPI `/api/v1/reversions` routes directly through same-origin relative URLs; do not
  add Next.js business routes, server actions, persistence, authorization, or credential forwarding.
* Preserve stable idempotency reuse after ambiguous transport failures and never automatically
  replay a mutation when the outcome is unknown.
* Clearly state that conversion is local and does not perform OCR; present scanned/image-only PDF
  failures without offering or invoking hosted Firecrawl OCR.
* Describe the workflow as CPU-only and low-compute without making an unmeasured speed or resource
  guarantee.
* Render authenticated/loading/session-expiry/backend-unavailable states consistently with the
  existing production shell.
* Add strict TypeScript, component, transport, accessibility, responsive-layout, browser-behavior,
  and coverage-gate tests without regressing Convert, Templates, or administration workflows.

## Dependencies

* T60
* T61
* T64
* T67
* T71

## Implementation boundary

* Own the Revert navigation entry, experimental visual treatment, `/revert` presentation, typed
  client contract, and frontend tests.
* T67 is a hard dependency. Start only after T67 has merged its own normative package-manager,
  bootstrap, workspace, command, and lockfile decision, then use that established contract without
  redesigning it or migrating the isolated Mermaid toolchain. This ticket does not select pnpm,
  Corepack, or another replacement while the current npm contract remains authoritative.
* Do not duplicate FastAPI validation or business behavior and do not add OCR.
* Do not change the existing Convert workflow except for the shared navigation entry.

## Quality requirements

* Meet the existing CSP, CSRF, cookie-stripping, same-origin, accessibility, rootless-frontend, and
  browser coverage contracts.
* Keep user-facing text and repository artifacts in English.
* Final two-profile rootless browser E2E remains blocking in T73, not waived.

## Progress

* 2026-09-03: Created from the approved feasibility decomposition; blocked by T71.
* 2026-09-03: User-facing scope now communicates CPU-only, low-compute processing without an
  unsupported performance claim.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
