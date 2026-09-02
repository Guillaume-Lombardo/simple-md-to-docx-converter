---
ticket: T65
linear_id: G1L-531
linear_url: https://linear.app/g1lom/issue/G1L-531/t65-expose-authoritative-frontend-runtime-metadata
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T65 - Expose authoritative frontend runtime metadata

## Objective

Expose the authoritative runtime metadata required by the Next.js conversion and administration workspaces without duplicating backend configuration or selection logic in the browser.

## Acceptance criteria

* Add an authenticated domain-specific conversion-options read contract returning the configured positive conversion upload limit in bytes, the resolved immutable template and exact version when selected, and a stable `pandoc_default`, `preferred`, or `system_fallback` selection source.
* Add an authenticated template-administration context returning the current user's preferred template identifier, the system fallback template identifier, and the configured positive template upload limit in bytes.
* Extend the administrator-only session-policy read response with the operator-configured absolute session lifetime ceiling in exact seconds.
* Keep FastAPI authoritative for configuration, template resolution, authentication, authorization, and session policy; expose no secrets, internal paths, or deployment credentials.
* Preserve backward compatibility through additive OpenAPI changes and regenerate and validate the canonical OpenAPI artifact and generated bindings.
* Preserve CLI parity by exposing conversion options through a machine-readable `markweave` read command, exposing template context through the template CLI, and displaying the absolute lifetime ceiling through the existing session-policy CLI.
* Cover authorization, null/default/fallback/preferred resolution, immutable template version reporting, configured bounds, and both SQLite and PostgreSQL profiles.
* Add or update integration coverage and final rootless-image E2E assertions for both profiles in hosted GitHub Actions.
* Do not implement the T62 or T63 Next.js workspaces in this ticket.

## Dependencies

* T45
* T57
* T59
* T61

## Implementation boundary

* Own the additive FastAPI/OpenAPI contracts, generated client bindings, supported CLI read surfaces, backend tests, and relevant documentation.
* T62 and T63 own the consuming Next.js workflows.
* Do not add a generic frontend bootstrap endpoint or make Next.js authoritative for backend policy.

## Quality requirements

* Preserve FastAPI as the sole business, authentication, authorization, persistence, and job-processing backend.
* Add automated tests for every introduced behavior and keep the applicable frontend and Python coverage gates.
* Run local checks without 1Password or unavailable external services.
* Run PostgreSQL, RustFS, integration, and final rootless-image E2E validation through the hosted GitHub Actions matrices.
* Keep repository artifacts and user-facing text in English.

## Progress

* 2026-09-02: Created after product approval of the domain-specific API and CLI-parity approach. Implementation started from verified `main` at `cd9705dce86ca2decdb5acb99067290546af3ada` on `feat/T65-frontend-runtime-metadata`; hosted GitHub Actions owns service-backed integration and final-image E2E validation without 1Password.
* 2026-09-02: Implemented authenticated `conversion-options` and `template-context` contracts with atomic template/version/source resolution, added the administrator absolute-session ceiling, CLI parity, canonical OpenAPI and generated bindings, cross-profile contracts, final-image assertions, and operator documentation. Local SQLite/API/CLI/frontend and no-external-service suites pass; PostgreSQL, RustFS, and final-image execution remains assigned to hosted GitHub Actions.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
