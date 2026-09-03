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
* Build an accessible `/revert` workspace with file selection and drag-and-drop, an authoritative
  supported-format hint, bounded client validation, submission, progressive polling/backoff,
  status, cancellation, expiration, safe errors, and result download.
* Call the FastAPI `/api/v1/reversions` routes directly through same-origin relative URLs; do not
  add Next.js business routes, server actions, persistence, authorization, or credential forwarding.
* Preserve stable idempotency reuse after ambiguous transport failures and never automatically
  replay a mutation when the outcome is unknown.
* Clearly state that conversion is local and does not perform OCR; present scanned/image-only PDF
  failures without offering or invoking hosted Firecrawl OCR.
* Render authenticated/loading/session-expiry/backend-unavailable states consistently with the
  existing production shell.
* Add strict TypeScript, component, transport, accessibility, responsive-layout, browser-behavior,
  and coverage-gate tests without regressing Convert, Templates, or administration workflows.

## Dependencies

* T60
* T61
* T64
* T71

## Implementation boundary

* Own the Revert navigation entry, experimental visual treatment, `/revert` presentation, typed
  client contract, and frontend tests.
* Do not duplicate FastAPI validation or business behavior and do not add OCR.
* Do not change the existing Convert workflow except for the shared navigation entry.

## Quality requirements

* Meet the existing CSP, CSRF, cookie-stripping, same-origin, accessibility, rootless-frontend, and
  browser coverage contracts.
* Keep user-facing text and repository artifacts in English.
* Final two-profile rootless browser E2E remains blocking in T73, not waived.

## Progress

* 2026-09-03: Created from the approved feasibility decomposition; blocked by T71.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
