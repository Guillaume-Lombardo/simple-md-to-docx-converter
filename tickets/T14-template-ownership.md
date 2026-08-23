---
ticket: T14
linear_id: G1L-325
linear_url: https://linear.app/g1lom/issue/G1L-325/
status: In Progress
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

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
