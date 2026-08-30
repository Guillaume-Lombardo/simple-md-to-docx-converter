---
ticket: T41
linear_id: G1L-414
linear_url: https://linear.app/g1lom/issue/G1L-414/t41-decompose-the-fastapi-application-module
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T41 - Decompose the FastAPI application module

## Objective

Split the oversized FastAPI composition module into stable routers, schemas, dependencies, error handling, and lifecycle components without changing the HTTP contract.

## Acceptance criteria

* Extract auth, conversion, template, administration, audit/observability, schema, error, dependency, and lifecycle responsibilities behind explicit module boundaries.
* Keep `create_app` as a small composition root and preserve dependency-injection seams used by deterministic tests.
* Preserve routes, OpenAPI, status codes, headers, cookies, CSRF/origin behavior, stable errors, correlation, ownership, and component shutdown semantics.
* Avoid circular imports and compatibility shims that indefinitely preserve the old internal layout.
* Pass focused contract comparison, unit, functional, integration, browser, and both-profile final-image tests with no behavior change.

## Dependencies

* T44
* T06

## Implementation boundary

* Own `app.py` decomposition and new HTTP-layer modules/tests.
* Do not change routes or business contracts and do not edit CLI modules, worker orchestration, or persistence internals.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-30: Implementation started on `refactor/T41-decompose-fastapi` from `becfee1b`. The work owns the FastAPI composition root and new HTTP-layer modules/tests while preserving the complete HTTP, OpenAPI, dependency-injection, observability, and shutdown contracts.
* 2026-08-30: Decomposed the 2,178-line application module into explicit HTTP components and a 98-line composition root. Focused tests pass, Ruff and ty pass, and both storage profiles retain the baseline 49-route manifest and OpenAPI digest. The broad local suite passed 1,900 tests at 94.98% coverage; PostgreSQL/S3 tests require unavailable local service variables. Both final-image profiles passed service and security workflows, while their browser journeys ended in environment-sensitive UI/template timeouts after reaching the expected pages and API routes.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
