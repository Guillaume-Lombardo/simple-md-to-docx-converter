---
ticket: T62
linear_id: G1L-526
linear_url: https://linear.app/g1lom/issue/G1L-526/t62-migrate-the-conversion-workflow-to-nextjs
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T62 - Migrate the conversion workflow to Next.js

## Objective

Migrate the complete asynchronous conversion browser workflow to the Next.js frontend with feature and failure parity.

## Acceptance criteria

* Support accessible file selection and drag-and-drop for Markdown and ZIP uploads, optional immutable-template search/selection, Pandoc-default mode, and DOCX, PDF, or combined output.
* Preserve bounded client validation, multipart submission, stable idempotency reuse after ambiguous transport failures, and reset behavior when request-defining input changes.
* Preserve progressive polling, transient-error recovery, generation/abort fencing, cancellation through a terminal state, expiration, recent-job reopening, progress and step presentation, safe errors, and result downloads with server-provided filenames.
* Preserve all FastAPI authorization, quota, capacity, cache-control, content-disposition, and error-envelope behavior without reproducing business decisions in Next.js.
* Meet accessibility requirements for keyboard operation, focus, labels, live regions, progress, reduced motion, responsive layout, and supported browser behavior.
* Add unit/component, contract, integration, real-browser, and final rootless-image E2E coverage for success, failure, cancellation, expiration, download, session expiry, restart recovery, authorization, and concurrency in both profiles.
* Keep the legacy conversion page available until T64 completes the verified cutover.

## Dependencies

* T61
* T16
* T57
* T65

## Implementation boundary

* Own the Next.js conversion page and its frontend tests.
* Do not change conversion-domain or job-state contracts except through a separately reviewed FastAPI/OpenAPI change.

## Quality requirements

* Preserve FastAPI as the sole business, authentication, authorization, persistence, and job-processing backend.
* Add automated tests for every introduced behavior and keep the applicable frontend and Python coverage gates.
* Cover every affected real boundary with integration tests and every delivered browser workflow with final rootless-image E2E tests for both storage profiles.
* Keep repository artifacts and user-facing text in English.
* Run all applicable canonical formatting, linting, type-checking, contract, browser, Python, container, and E2E checks.

## Progress

* 2026-09-01: Created with exact behavioral parity to the delivered asynchronous conversion workflow and no backend-domain rewrite.
* 2026-09-02: Blocked on T65 so the Next.js workspace can consume authoritative conversion upload limits and resolved immutable-template selection metadata instead of duplicating backend policy.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
