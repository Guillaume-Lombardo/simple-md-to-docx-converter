---
ticket: T14
linear_id: G1L-325
linear_url: https://linear.app/g1lom/issue/G1L-325/
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T14 - Add template ownership, search, and user preferences

## Objective

Add immutable ownership, global visibility, search, preferences, fallback templates, and cross-profile authorization tests.

## Acceptance criteria

- The implementation satisfies the T14 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.
- Template ownership is immutable and derives from the authenticated user identifier.
- Every active template is globally visible to authenticated users; mutations remain restricted to the owner or a global administrator, and the service exposes the audit boundary for administrator intervention.
- Search is deterministic and paginated, with filters for name, description, owner, and status.
- Each user can select one preferred active template, while a single active system fallback resolves selection when no valid preference exists.
- Stable domain, service, and repository ports preserve ownership for later T15 versioned mutation and object-key workflows without implementing T15 history, download, ETag, replacement, restoration, or deletion behavior.
- SQLite and PostgreSQL pass the same repository and authorization contracts, including two regular users, one administrator, relevant constraints, failures, and races.
- T14 introduces no HTTP route, UI, or other user-visible operational workflow; final-image E2E is therefore not applicable to this ticket rather than deferred or waived.

## Dependencies

- T06
- T12

## Progress

- 2026-08-23: Started implementation on `feat/T14-template-ownership` from `main` at `a624407` after confirming Linear project, team, priority, objective, acceptance criteria, and dependency parity. T06 and T12 are both `Done`; T14 has no remaining dependency blocker. Scope is limited to domain and cross-profile persistence foundations for ownership, visibility, authorization, search, preferences, fallback selection, and administrator audit boundaries; T15 version/content mutation APIs and T16/T17 UI remain deferred to their own tickets.
- 2026-08-23: Implemented frozen template identities with database-enforced immutable owners, globally visible active templates, owner/admin visibility and mutation authorization, explicit administrator-intervention audit context, deterministic NFKC/casefold search with name/description/owner/status filters and pagination, transactional per-user preference and singleton fallback selection, and active preference-to-fallback resolution. Added the second Alembic revision, shared SQLite/PostgreSQL contracts, real constraint/restart/outage/concurrency coverage, two-user-plus-administrator functional authorization tests, and English architecture/storage/template documentation. No template route, UI, content/version row, download, ETag, replacement, restoration, archive/delete command, or persistent audit implementation was added; T14 therefore introduces no final-image E2E-applicable workflow.
- 2026-08-23: Final local validation passes `uv sync --all-groups`, `uv lock --check`, Ruff format/lint, `ty`, both 184-test canonical Pytest commands at 98.30% application coverage, the 143-test unit slice at 95.64% application and 93.51% branch-only coverage, and 98.96% changed executable application-line coverage. Exact functional, standalone-storage, and distributed-storage domains pass 15, 9, and 10 tests respectively with real PostgreSQL and RustFS; repository CI validation, checksum-verified actionlint v1.7.12, the hash-constrained sdist/wheel build, and `git diff --check` also pass. The only warning is Starlette's existing non-blocking TestClient/httpx2 deprecation notice.
- 2026-08-23: Independent-review corrections add an actor-based creation input with no owner field, force every created identity to derive its immutable owner from the authenticated actor, and exercise forged-owner resistance plus the complete two-user-and-administrator service authorization and selection contract over both real SQLite and PostgreSQL repositories.
- 2026-08-23: Correction validation passes the focused unit/SQLite tests (4 tests), the real PostgreSQL template contract (1 test), Ruff formatting and linting, `ty`, the 144-test unit slice at 95.67% application coverage, and both 185-test canonical Pytest commands at 98.31% application coverage with ephemeral PostgreSQL and RustFS services matching CI. Independent re-review found no findings, confirmed 100% changed-line coverage (299/299), and approved publication. The existing Starlette TestClient/httpx2 deprecation warning remains non-blocking.
- 2026-08-24: GitHub PR #27 exact rebased head `22fcf501ca5e0079e27cd46711fc499cf92ea7e3` passed PR CI run `32669541287`, retained independent approval with no findings and 100% changed-line coverage (299/299), and was squash-merged into `main` as `c296d458b2d64c3ee1d9cfbb6f65e8f86ff440b9`. Exact-main run `32669621800` passed the functional, standalone-storage, distributed-storage, and protected-gate jobs. T14 is therefore verified `Done` on `main`. Its only remaining observation is the existing non-blocking Starlette TestClient/httpx2 deprecation warning; final-image E2E is genuinely not applicable because T14 adds no route, UI, or user-visible operational workflow.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
