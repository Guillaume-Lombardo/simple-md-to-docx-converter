---
ticket: T59
linear_id: G1L-523
linear_url: https://linear.app/g1lom/issue/G1L-523/t59-add-an-administrator-controlled-idle-session-policy
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T59 - Add an administrator-controlled idle session policy

## Objective

Allow administrators to configure the system-wide idle session duration enforced by FastAPI: the inactivity period after which a user must authenticate again.

## Acceptance criteria

* Keep the existing 30-minute idle duration as the default when no persisted administrator override exists.
* Obtain and record explicit product approval for the minimum and maximum administrator-selectable idle durations before implementation; do not infer security bounds.
* Keep the operator-configured absolute session lifetime as a hard ceiling that an administrator cannot exceed.
* Persist one versioned system-wide idle-session policy in both SQLite and PostgreSQL profiles and expose authenticated administrator-only read/update endpoints under `/api/v1`.
* Make FastAPI enforce the current effective policy on every session validation so a previously issued cookie cannot bypass a tightened timeout; an already expired or revoked session is never revived by a later relaxation.
* Define deterministic concurrency semantics with an ETag/revision precondition and reject stale updates without partial state.
* Audit actor, old value, new value, revision, and operation without recording tokens or credentials.
* Preserve session revocation on logout, deactivation, password reset, password renewal, idle expiry, and absolute expiry.
* Regenerate and validate the canonical OpenAPI artifact and update authentication, administration, configuration, backup, and recovery documentation.
* Test default/bootstrap behavior, authorized and forbidden access, bounds, stale writes, immediate tightening, non-revival after relaxation, restart persistence, backup/restore, and both storage profiles with two users and one administrator.

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

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
