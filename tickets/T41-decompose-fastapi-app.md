---
ticket: T41
linear_id: G1L-414
linear_url: https://linear.app/g1lom/issue/G1L-414/t41-decompose-the-fastapi-application-module
status: Done
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
* 2026-08-30: Decomposed the 2,178-line application module into explicit HTTP components and a 98-line composition root. Focused tests pass, Ruff and ty pass, and both storage profiles retain the baseline 49-route manifest and OpenAPI digest. The broad local suite passed 1,900 tests at 94.98% coverage, dedicated PostgreSQL 18 and RustFS integrations passed 35 tests, and hardened-image browser tests passed. After integrating T43's reviewed E2E engine timeout, the complete standalone and distributed final-image workflows passed, including service, security, browser, restart, recovery, checkpoint, and origin checks.
* 2026-08-30: Addressed independent review by replacing opaque hash-only contract checks with a checked-in, pretty-printed OpenAPI test fixture and a readable 49-route manifest covering path, methods, name, schema visibility, status code, and effective response class. FastAPI default placeholders are resolved to their actual response class, including for hidden routes. Both storage profiles are compared recursively with exact JSON-path failure messages. These T41 regression fixtures remain separate from T45's durable OpenAPI artifact, compatibility policy, and CI workflow. The 35 HTTP-adapter tests and a 1,933-test relevant local suite pass at 95.40% coverage; Ruff and ty pass.
* 2026-08-30: Addressed CodeRabbit lifecycle findings by ensuring queue observations are cancelled even when no storage resources are owned and by guarding readiness-client configuration so a failure closes the already-created S3 client. Focused cleanup regressions and 131 relevant HTTP, storage, observability, and worker-metrics tests pass; Ruff and ty pass.
* 2026-08-30: Verified on `main` after PR #119 merged as `224d1115331ecf80f3b3287976f6d72662a5d01b`. Exact-main CI run `33320087352` completed successfully after the CodeRabbit contract-readability and resource-cleanup findings were resolved; all T41 acceptance criteria are verified.

## Coordination

* Status: Done.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
