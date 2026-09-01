---
ticket: T59
linear_id: G1L-523
linear_url: https://linear.app/g1lom/issue/G1L-523/t59-add-an-administrator-controlled-idle-session-policy
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T59 - Add an administrator-controlled idle session policy

## Objective

Allow administrators to configure the system-wide, role-specific idle session durations enforced by FastAPI: the inactivity periods after which standard users and administrators must authenticate again.

## Acceptance criteria

* When no persisted administrator override exists, default standard-user sessions to 30 minutes of inactivity and administrator sessions to 15 minutes of inactivity.
* Accept whole-minute standard-user idle durations from 5 through 300 minutes, inclusive.
* Accept whole-minute administrator idle durations from 5 through 60 minutes, inclusive.
* Keep the operator-configured absolute session lifetime as a hard ceiling that an administrator cannot exceed.
* Persist one versioned system-wide idle-session policy containing both role-specific durations in SQLite and PostgreSQL profiles and expose authenticated administrator-only read/update endpoints under `/api/v1`.
* Make FastAPI enforce the duration for the session user's current effective role on every session validation so a previously issued cookie cannot bypass a tightened timeout or role change; an already expired or revoked session is never revived by a later relaxation.
* Define deterministic concurrency semantics with an ETag/revision precondition and reject stale updates without partial state.
* Audit actor, old value, new value, revision, and operation without recording tokens or credentials.
* Preserve session revocation on logout, deactivation, password reset, password renewal, idle expiry, and absolute expiry.
* Regenerate and validate the canonical OpenAPI artifact and update authentication, administration, configuration, backup, and recovery documentation.
* Test both role defaults, whole-minute granularity, inclusive bounds, authorized and forbidden access, stale writes, immediate tightening, role changes, non-revival after relaxation, restart persistence, backup/restore, and both storage profiles with two users and one administrator.

## Dependencies

* T58
* T06
* T12
* T19
* T45

## Implementation boundary

* Own the FastAPI API, domain policy, persistence, migration, audit, OpenAPI, and backend tests.
* Do not implement the Next.js administration control in this ticket.
* Do not allow Next.js or browser state to become authoritative for session expiry.

## Quality requirements

* Preserve FastAPI as the sole business, authentication, authorization, persistence, and job-processing backend.
* Add automated tests for every introduced behavior and keep the applicable frontend and Python coverage gates.
* Cover every affected real boundary with integration tests and every delivered browser workflow with final rootless-image E2E tests for both storage profiles.
* Keep repository artifacts and user-facing text in English.
* Run all applicable canonical formatting, linting, type-checking, contract, browser, Python, container, and E2E checks.

## Progress

* 2026-09-01: Created with the administrator setting scoped to idle reauthentication time. The existing 30-minute default remains fixed; minimum and maximum selectable values require a separate explicit product decision before implementation.
* 2026-09-01: Product approved role-specific whole-minute policies: standard users default to 30 minutes with an inclusive 5–300 minute range; administrators default to 15 minutes with an inclusive 5–60 minute range. Implementation started from verified `main` at `049146de248e684acfb170aa526030f2ca0c84cb` on `feat/T59-role-idle-session-policy`.
* 2026-09-01: Implemented the versioned role-specific policy, administrator-only ETag API, immutable audit trail, SQLite and PostgreSQL persistence, current-role session enforcement, OpenAPI contract, recovery coverage, documentation, and final-image workflow assertions. The locally runnable suite passes with 2,167 tests and 95.40% coverage; PostgreSQL, S3, and real final-image checks remain unavailable in the current environment because their required service configuration is absent.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
