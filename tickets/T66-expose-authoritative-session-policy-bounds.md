---
ticket: T66
linear_id: G1L-532
linear_url: https://linear.app/g1lom/issue/G1L-532/t66-expose-authoritative-session-policy-bounds
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T66 - Expose authoritative session-policy bounds

## Objective

Expose the authoritative role-specific session-policy bounds, defaults, and minute granularity required by the T63 administration UI without duplicating FastAPI policy values in the frontend.

## Acceptance criteria

* Extend the administrator-only session-policy read response additively with authoritative standard-user and administrator idle-duration metadata: inclusive minimum, default, and maximum values in minutes.
* Expose the one-minute policy granularity explicitly.
* Source response metadata from the backend policy constants or configuration; do not duplicate response literals.
* Preserve existing effective values, revision, absolute-lifetime ceiling, authorization, persistence, audit, optimistic concurrency, and session-enforcement behavior.
* Preserve CLI parity for human-readable and JSON session-policy reads.
* Regenerate and validate canonical OpenAPI and generated TypeScript bindings.
* Cover both role profiles through API and CLI contracts; maintain hosted integration and final rootless-image E2E assertions across both storage profiles.
* Do not implement the T63 Next.js administration UI.

## Dependencies

* T59
* T65

## Implementation boundary

* Own additive FastAPI/OpenAPI session-policy metadata, corresponding CLI reads, generated artifacts, backend/contract coverage, and related documentation.
* T63 owns the consuming Next.js workflow and its repository mirror.

## Quality requirements

* Preserve FastAPI as the sole business, authentication, authorization, persistence, and job-processing backend.
* Add automated tests for every introduced behavior and keep applicable frontend and Python coverage gates.
* Run local checks without 1Password or unavailable external services; hosted GitHub Actions runs PostgreSQL, RustFS, integration, and final rootless-image E2E validation.
* Keep repository artifacts and user-facing text in English.

## Progress

* 2026-09-02: Created as the High-priority T63 prerequisite. Linear issue G1L-532 is In Progress, depends on T59 and T65, and blocks T63.
* 2026-09-02: Implemented additive administrator-only role-bound metadata sourced from authentication policy constants, CLI JSON/human parity, canonical OpenAPI and TypeScript bindings, contract coverage, final-image workflow assertions, and documentation. Targeted API/CLI/OpenAPI tests, root browser checks, frontend quality gate, Ruff, and ty pass locally. The canonical Python suite has 2,246 passing tests and 95.57% coverage; PostgreSQL, RustFS, and final rootless-image execution remain assigned to hosted GitHub Actions because local service configuration is unavailable.
* 2026-09-02: Completed on `main` as `27b166adb938f791e1ac4dba06c61dc25775c546` through PR #171. Post-merge GitHub Actions run `33622559406` passed all 11 jobs, including the gate, both storage profiles, and final rootless-image E2E suites.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
