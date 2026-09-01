---
ticket: T64
linear_id: G1L-528
linear_url: https://linear.app/g1lom/issue/G1L-528/t64-cut-over-and-harden-the-nextjs-frontend
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T64 - Cut over and harden the Next.js frontend

## Objective

Complete the production cutover from FastAPI-rendered pages to the Next.js frontend and harden, document, and verify the resulting two-runtime system.

## Acceptance criteria

* Route browser pages and assets to the reviewed Next.js runtime while routing both exact `/api/v1` and `/api/v1/**`, downloads, OpenAPI, health, readiness, and metrics directly to FastAPI on the same origin according to T58.
* Prove login, password renewal, conversion, template, user, and session-policy parity and rehearse rollback while the candidate branch still contains the legacy renderer; then remove the legacy Python renderers/assets, build and serialize the matched final images exactly once, run acceptance against those exact bytes, publish the same bytes, and cut over to their verified digests without a post-test rebuild.
* Build reproducible rootless frontend and backend artifacts with arbitrary-UID support, read-only roots, no added capabilities, bounded writable areas, pinned inputs, SBOMs, provenance, vulnerability gates, and verified immutable publication identities.
* Update standalone/distributed deployment examples, Compose and both quickstarts, routing, readiness, startup/rollback cleanup, resource budgets, backup/recovery scope, monitoring, and operational documentation.
* Ensure frontend unavailability cannot corrupt backend state, backend unavailability produces bounded safe UI failures, and readiness distinguishes frontend routing from backend/service readiness.
* Order a normalized public content-free `404` denial for `/_frontend/health/**` and decoded/case-varied equivalents before the frontend catch-all; prove only direct internal Service probes on the separate probe port can reach the exact live/ready endpoints, and cover their success/failure plus public exact, descendant, encoded, and case-variant denial.
* Verify the custom-server 16 KiB header ceiling with an empty overflow `431`, exact 128/129 admission boundary, empty saturation/draining `503`s, response accounting, and 30-second SIGTERM bound in the final image.
* Verify exact Next.js `16.3.4` dynamic nonce CSP behavior with a fresh non-reused nonce shared by `script-src` and `style-src`, every bootstrap script and inline style element nonced, no inline style attribute or unnonced inline style, no eval, and no `unsafe-inline`/`unsafe-eval`; verify exact router-owned HSTS and Permissions-Policy headers on frontend, API, error, and download responses.
* Through the production router, verify that page and asset requests reach the frontend without `Cookie` and all frontend `Set-Cookie` fields are removed, while incoming `Cookie` and every outgoing `Set-Cookie` field are preserved unchanged for exact `/api/v1`, an `/api/v1/**` descendant, and a representative public operational route.
* Run the complete frontend quality gates, OpenAPI compatibility gate, Python canonical checks, container smoke tests, and final-image Playwright E2E workflows for both storage profiles with two users and one administrator.
* Cover login/renewal/logout, session-policy tightening and expiry, conversion success/failure/cancellation/expiration/download/recovery/concurrency, template lifecycle, account administration, authorization failures, CSRF/CSP/Origin enforcement, restart recovery, and failure-only sanitized artifacts.
* Cover replacement expected-font editing and explicit clearing through the comma-separated field without omitting the API field.
* Update the normative specification, architecture, authentication, administration, UI, deployment, configuration, operations, recovery, development, and release documentation.
* Do not declare completion until the cutover is verified on `main` and the published runtime identities are consistently pinned.

## Dependencies

* T62
* T63
* T20
* T21
* T22

## Implementation boundary

* Own routing cutover, legacy frontend removal, production artifacts, deployment integration, full E2E acceptance, documentation, and release alignment.
* Do not change FastAPI business contracts beyond separately reviewed and versioned API changes.

## Quality requirements

* Preserve FastAPI as the sole business, authentication, authorization, persistence, and job-processing backend.
* Add automated tests for every introduced behavior and keep the applicable frontend and Python coverage gates.
* Cover every affected real boundary with integration tests and every delivered browser workflow with final rootless-image E2E tests for both storage profiles.
* Keep repository artifacts and user-facing text in English.
* Run all applicable canonical formatting, linting, type-checking, contract, browser, Python, container, and E2E checks.

## Progress

* 2026-09-01: Created as the sole production cutover after the authentication, conversion, administration, and backend session-policy work is complete and independently verified.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
