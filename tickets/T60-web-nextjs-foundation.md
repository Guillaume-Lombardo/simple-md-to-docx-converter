---
ticket: T60
linear_id: G1L-524
linear_url: https://linear.app/g1lom/issue/G1L-524/t60-build-the-web-nextjs-typescript-and-tailwind-foundation
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T60 - Build the web Next.js TypeScript and Tailwind foundation

## Objective

Create the production-ready Next.js, TypeScript, and Tailwind CSS application foundation under `web/` without changing user workflows.

## Acceptance criteria

* Create an isolated `web/` application using the architecture, runtime, package-manager, and version policies approved by T58.
* Enable strict TypeScript, linting, formatting, deterministic lockfile installation, reproducible production builds, and Tailwind CSS with a small accessible design-token foundation.
* Generate typed frontend bindings for the production runtime and test fixtures from the canonical OpenAPI contract; fail CI when generated contract artifacts are stale, and do not hand-maintain a divergent API model.
* Provide one typed API transport for JSON, multipart uploads, downloads, error envelopes, ETags, CSRF headers, idempotency keys, cancellation, and request aborts.
* Establish accessible application-shell, form, alert, loading, progress, dialog, table/list, and navigation primitives without introducing a separate business backend.
* On exact Next.js `16.3.4` production dynamic rendering, enforce the reviewed nonce CSP without `unsafe-inline` or `unsafe-eval`; prove every response receives a fresh nonce, cached output never reuses one, `script-src` and `style-src` contain that same nonce, every framework bootstrap script and inline style element carries it, no inline style attribute or unnonced inline style is emitted, and no generated page or asset requires eval. This gate blocks T60 and is repeated against the final image in T64; there is no framework exception.
* Implement the supported `web/server.mjs` custom production server without Next.js `output: "standalone"`; enforce 16 KiB request headers with a zero-length overflow `431`, the exact 128/129 per-replica admission boundary, zero-length saturation/draining `503` responses, exact finish/close accounting, and bounded 30-second SIGTERM draining with no API proxying.
* Add blocking routing-fixture tests proving that page and asset upstream requests contain no `Cookie` header and all frontend `Set-Cookie` response fields are removed, while both directions survive unchanged for exact `/api/v1`, an `/api/v1/**` descendant, and a representative direct FastAPI operational route.
* Add frontend unit/component coverage thresholds at least as strict as the existing JavaScript 90% line, branch, and function gates.
* Integrate deterministic frontend dependency, build, type, lint, test, cache, and affected-path selection into CI.
* Add a minimal rootless production smoke test for the frontend runtime and its two internal health endpoints on the Service-only probe port, including normalized public-router denial of the complete `/_frontend/health/**` prefix and decoded/case-varied equivalents before the catch-all.
* Leave the current FastAPI-rendered pages as the production UI until the cutover ticket.

## Dependencies

* T58
* T45

## Implementation boundary

* Own `web/`, shared UI primitives, generated API bindings, frontend CI, and the frontend runtime smoke test.
* Do not migrate complete login, conversion, template, or account workflows in this ticket.

## Quality requirements

* Preserve FastAPI as the sole business, authentication, authorization, persistence, and job-processing backend.
* Add automated tests for every introduced behavior and keep the applicable frontend and Python coverage gates.
* Cover every affected real boundary with integration tests and every delivered browser workflow with final rootless-image E2E tests for both storage profiles.
* Keep repository artifacts and user-facing text in English.
* Run all applicable canonical formatting, linting, type-checking, contract, browser, Python, container, and E2E checks.

## Progress

* 2026-09-01: Created as the frontend foundation after T58; it deliberately leaves the existing production pages active.
* 2026-09-01: Review clarified that production runtime code must use generated typed bindings; fixtures are generated from the same OpenAPI contract for tests only.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
