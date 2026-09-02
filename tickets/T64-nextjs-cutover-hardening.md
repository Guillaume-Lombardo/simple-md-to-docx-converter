---
ticket: T64
linear_id: G1L-528
linear_url: https://linear.app/g1lom/issue/G1L-528/t64-cut-over-and-harden-the-nextjs-frontend
status: In Progress
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
* Verify exact Next.js `16.3.4` dynamic nonce CSP behavior on every dynamically rendered HTML document with a fresh non-reused nonce shared by `script-src` and `style-src`, every bootstrap script and inline style element nonced, no inline style attribute or unnonced inline style, no eval, and no `unsafe-inline`/`unsafe-eval`; prove content-hashed `/_next/static/**` assets and non-HTML/content-free responses, including empty `431`/`503` custom-server failures, receive no generated nonce CSP; verify exact router-owned HSTS and Permissions-Policy headers on frontend, API, error, and download responses.
* Through the production router, verify every frontend route and method strips upstream `Cookie` and all downstream frontend `Set-Cookie` fields, covering a named-page GET, framework-asset GET, unknown-path GET, POST to a named page, PATCH to an unknown catch-all path, and multiple response fields; verify incoming `Cookie` and every outgoing `Set-Cookie` field are preserved unchanged for exact `/api/v1`, an `/api/v1/**` descendant, and a representative public operational route.
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
* 2026-09-02: Started from exact verified GitHub `main` SHA `49dbd5913f2cea4d6c0bde9a608d29e83b3c528d` after T63 completed on `main`. The approved delivery uses release version `0.6.0`, keeps both quickstarts on loopback HTTP, implements the reviewed operator routing contract without bundling TLS certificates, and preserves exact HSTS without `includeSubDomains` or `preload`.
* 2026-09-02: Implemented the pre-removal routing boundary: the rootless frontend image now packages the production same-origin router; candidate Compose overlays and both quickstarts require a matched immutable backend/frontend pair; deployment examples separate frontend pages, internal probes, router TLS, and FastAPI services. Production-router unit/contract/coverage, frontend build/production, rootless paired-image smoke, Compose rendering, harness, and deployment-asset checks pass locally. The legacy renderer remains present until the candidate branch completes both hosted storage-profile workflows, preserving a reproducible rollback point.
* 2026-09-02: Added the paired release boundary in `24110b2`: one trusted run builds and serializes both images once, exercises both storage profiles against those local exact bytes, retains the staged pair before registry mutation, preflights both GHCR packages before copying either, and binds their receipts, SBOMs, scans, source SHA, version, and frontend lockfile in one checksummed manifest. A failed partial publication can recover only from the immutable staged artifact without rebuilding. The focused CI/container/release suite passes with 234 tests plus Ruff, ty, ShellCheck, syntax, and existing-image rootless smoke.
* 2026-09-02: Hosted pre-removal run `33681610322` at exact router SHA `b0980901dc572a4cbcf0e78a60a013b750329dee` passed the complete functional Next.js journey in both storage profiles and every non-E2E domain. Both E2E jobs then failed only during teardown because Podman attempted to remove a backend network-namespace parent before its router child. The candidate now removes the dependent router in a separate operation before every parent removal and in the exit trap; 376 focused harness, CI, release, Compose, and quickstart tests cover the correction. Legacy removal remains gated on a new exact-head dual-profile hosted pass.
* 2026-09-02: Exact-head rerun `33684103607` passed the same functional matrix through durable-result preparation in both profiles, then exposed that restarting the backend invalidates its child router's shared network namespace. The harness now treats restart separately from terminal cleanup: remove the router, restart and probe the backend, then recreate and probe the router before recovery. Focused coverage remains 376 passing tests; another exact-head hosted pass is required before legacy removal.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
