---
ticket: T58
linear_id: G1L-522
linear_url: https://linear.app/g1lom/issue/G1L-522/t58-define-the-nextjs-frontend-migration-architecture
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T58 - Define the Next.js frontend migration architecture

## Objective

Define and approve the target architecture and migration contract for replacing the server-rendered browser frontend with Next.js, TypeScript, and Tailwind CSS under `web/`, while retaining FastAPI as the backend.

## Acceptance criteria

* Update the normative specification so `web/` owns browser pages and assets with Next.js, TypeScript, and Tailwind CSS, while FastAPI remains authoritative for exact `/api/v1`, `/api/v1/**`, authentication, authorization, persistence, conversions, templates, accounts, audit, health, readiness, metrics, and OpenAPI.
* Define one browser-visible origin and a reviewed routing contract for frontend pages and same-origin exact `/api/v1` and `/api/v1/**` requests; do not trust forwarded headers or duplicate business rules in Next.js route handlers.
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
* 2026-09-01: Reopened after review found that PR #155 recorded only high-level migration backlog scaffolding and did not satisfy the production topology, security boundary, runtime, release, rollback, or complete legacy-behavior inventory criteria. Linear G1L-522 and this repository mirror are `In Progress` while the missing architecture contract is completed.
* 2026-09-01: Completed the missing architecture contract locally: the normative specification and `docs/nextjs-migration-architecture.md` now select the separate stateless frontend topology, exact one-origin routes, digest-pinned UBI/Node/npm and exact framework toolchain, rootless budgets and probes, browser/API trust boundaries, matched two-image publication identity, staged cutover and release-level rollback, and the complete T61–T64 legacy parity inventory. Architecture, authentication, deployment, release, and upgrade guides carry the same decision. The ticket remains `In Progress` until review, merge, and exact-main verification; Linear was already corrected separately and was not modified by this change.
* 2026-09-01: Local verification passed for Markdown link integrity, deterministic OpenAPI freshness, Ruff formatting and linting, `ty`, the existing JavaScript suite with its independent coverage gates, and 14 focused documentation/version/legacy-Web tests. The broader default Python suite was not applicable to this documentation-only change and could not complete without the configured PostgreSQL/RustFS services; no container or final-image behavior changed in T58.
* 2026-09-01: Architecture review corrections make the contract implementable and remove remaining ambiguity: the public router denies normalized frontend-probe paths before catch-all while a separate Service-only port owns internal probes; T60 owns a supported custom server instead of incompatible standalone output for the 16 KiB/128-request/drain boundaries; the exact nonce CSP and router-owned HSTS/Permissions-Policy values are blocking tests; T64 rehearses rollback before legacy removal and builds, verifies, publishes, and deploys one set of final matched bytes; and replacement expected-font editing and explicit clearing are preserved. Scoped link, OpenAPI, Ruff, `ty`, JavaScript coverage, and 14 focused Python tests passed again.
* 2026-09-01: PR review follow-up closes three remaining routing and CSP gaps. The router now strips the complete `Cookie` header from every frontend-bound request and every frontend `Set-Cookie` response field while preserving both unchanged on exact `/api/v1`, `/api/v1/**`, and operational FastAPI routes. The production CSP uses the same fresh nonce in `script-src` and `style-src`, requires every bootstrap script and inline style element to carry it, and forbids style attributes or unnonced inline styles. Normative routing consistently names both the exact API base and its descendants. T60 fixture and T64 final-image tests are blocking. Scoped links, OpenAPI freshness, Ruff, `ty`, 23 JavaScript tests with all coverage gates, and 14 focused Python tests passed.
* 2026-09-01: A final terminology review aligned the CSP interception hook with the official Next.js 16 convention: T60 must use `web/proxy.ts` with the named `export function proxy`, and structural/build checks reject deprecated `middleware.ts` or a `middleware` export. Local links, OpenAPI freshness, Ruff format/lint, `ty`, and seven focused documentation/version tests passed.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
