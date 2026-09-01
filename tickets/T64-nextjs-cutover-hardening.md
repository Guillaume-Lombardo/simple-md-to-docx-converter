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

* Route browser pages and assets to the reviewed Next.js runtime while preserving same-origin `/api/v1`, downloads, OpenAPI, health, readiness, and metrics according to T58.
* Remove the legacy Python HTML renderers and native browser assets only after login, password renewal, conversion, template, user, and session-policy parity is proven; retain a documented rollback path for the release.
* Build reproducible rootless frontend and backend artifacts with arbitrary-UID support, read-only roots, no added capabilities, bounded writable areas, pinned inputs, SBOMs, provenance, vulnerability gates, and verified immutable publication identities.
* Update standalone/distributed deployment examples, Compose and both quickstarts, routing, readiness, startup/rollback cleanup, resource budgets, backup/recovery scope, monitoring, and operational documentation.
* Ensure frontend unavailability cannot corrupt backend state, backend unavailability produces bounded safe UI failures, and readiness distinguishes frontend routing from backend/service readiness.
* Run the complete frontend quality gates, OpenAPI compatibility gate, Python canonical checks, container smoke tests, and final-image Playwright E2E workflows for both storage profiles with two users and one administrator.
* Cover login/renewal/logout, session-policy tightening and expiry, conversion success/failure/cancellation/expiration/download/recovery/concurrency, template lifecycle, account administration, authorization failures, CSRF/CSP/Origin enforcement, restart recovery, and failure-only sanitized artifacts.
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
