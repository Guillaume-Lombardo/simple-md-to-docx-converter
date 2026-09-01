---
ticket: T58
linear_id: G1L-522
linear_url: https://linear.app/g1lom/issue/G1L-522/t58-define-the-nextjs-frontend-migration-architecture
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T58 - Define the Next.js frontend migration architecture

## Objective

Define and approve the target architecture and migration contract for replacing the server-rendered browser frontend with Next.js, TypeScript, and Tailwind CSS under `web/`, while retaining FastAPI as the backend.

## Acceptance criteria

* Update the normative specification so `web/` owns browser pages and assets with Next.js, TypeScript, and Tailwind CSS, while FastAPI remains authoritative for `/api/v1`, authentication, authorization, persistence, conversions, templates, accounts, audit, health, readiness, metrics, and OpenAPI.
* Define one browser-visible origin and a reviewed routing contract for frontend pages and same-origin `/api/v1` requests; do not trust forwarded headers or duplicate business rules in Next.js route handlers.
* Select and document the production topology, rootless runtime boundaries, health/readiness ownership, failure behavior, resource budgets, image/SBOM/publication model, and rollback path.
* Inventory every current login, password-renewal, conversion, template, user-administration, session-expiry, accessibility, and error behavior that the migration must preserve.
* Define the staged cutover so the existing frontend remains available until parity and final-image E2E verification are complete.
* Record maintained Node.js, Next.js, TypeScript, Tailwind CSS, and package-manager policies without introducing unreviewed floating production dependencies.
* Define CSP, CSRF, cookie, Origin, download, upload, caching, and browser-to-API trust boundaries.
* Produce no user-visible behavior change in this architecture ticket.

## Dependencies

* T45
* T20
* T21

## Implementation boundary

* Own the normative architecture, migration inventory, deployment decision, and ticket contracts.
* Do not implement the new frontend or alter backend behavior in this ticket.

## Quality requirements

* Preserve FastAPI as the sole business, authentication, authorization, persistence, and job-processing backend.
* Add automated tests for every introduced behavior and keep the applicable frontend and Python coverage gates.
* Cover every affected real boundary with integration tests and every delivered browser workflow with final rootless-image E2E tests for both storage profiles.
* Keep repository artifacts and user-facing text in English.
* Run all applicable canonical formatting, linting, type-checking, contract, browser, Python, container, and E2E checks.

## Progress

* 2026-09-01: Created after product approval of the Next.js, TypeScript, and Tailwind CSS migration target with FastAPI retained as the backend and `web/` reserved for the frontend.
* 2026-09-01: Completed and verified on `main`: PR #155 was squash-merged as `b2efcd37f4e9a9fbd60d6fd664f1901b27bb4dea`, and exact-main CI run `33505309785` passed. Linear G1L-522 is Done with the same evidence; this mirror is synchronized.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
