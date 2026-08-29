---
ticket: T30
linear_id: G1L-398
linear_url: https://linear.app/g1lom/issue/G1L-398/
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T30 - Provision users from CSV and require password renewal

## Objective

Provision local users from a CSV file at application startup, updating the password when a
normalized username already exists, and support an administrator-controlled requirement that a
user change their password immediately after successfully authenticating with the existing
password.

## Acceptance criteria

- A configured UTF-8 CSV is validated and applied before readiness; malformed,
  duplicate-normalized, unreadable, or insecurely structured input fails startup without partial
  database mutation.
- Missing users are created and existing normalized usernames receive the CSV password, with
  Argon2id hashes only in storage and no password in logs, errors, audit records, or durable files
  created by Markweave.
- Startup provisioning is transactionally atomic in both SQLite and PostgreSQL, safe under
  concurrent distributed startup, and repeated application of unchanged input has deterministic
  behavior.
- Existing-account password replacement increments the authentication version and revokes prior
  sessions.
- The CSV can require a password change on next login.
- A required user must first authenticate successfully with the old/current password, then can
  access only session inspection, logout, and password-renewal endpoints/page until renewal
  succeeds.
- Renewal requires the authenticated restricted session plus valid CSRF protection, validates and
  confirms the new password, commits it atomically, clears the requirement, invalidates the
  restricted session, and requires login with the new password.
- Administrators can set the force-change requirement through supported account management without
  learning the user's password.
- Unit, functional, real SQLite/PostgreSQL integration, browser, and final-rootless-image E2E tests
  cover happy paths and relevant validation, authorization, revocation, race, restart, and failure
  behaviors.
- Documentation and all repository and user-facing artifacts are in English.
- Ruff, `ty`, Pytest, JavaScript coverage, and every applicable canonical check pass.
- The project version is bumped from `0.3.5` to `0.4.0` and all release-bound version surfaces
  remain consistent.

## Dependencies

- T06
- T12
- T17
- T21

## Progress

- 2026-08-29: Created Linear G1L-398 and this repository mirror after confirming that T00-T29 were
  already allocated. Started implementation on `feat/T30-user-provisioning` from `origin/main` at
  `ff5d6fc`. Scope covers strict startup CSV provisioning, cross-profile atomic user upsert,
  password-change-required persistence and restricted-session renewal, administrator controls,
  browser/API behavior, documentation, and the minor release bump to `0.4.0`.
- 2026-08-29: Implemented strict UTF-8 CSV startup provisioning through
  `MD_CONVERTER_USER_PROVISIONING_FILE`, atomic SQLite/PostgreSQL account upsert with session
  invalidation, persisted forced-renewal state, restricted browser/API sessions, self-service
  renewal, administrator controls, migration `20260829_14`, documentation, and version `0.4.0`.
  Ruff, formatting, `ty`, JavaScript coverage, and the focused unit/functional/SQLite suites pass;
  the canonical non-document-engine run reached 1612 passing tests and the full run reached 1619
  passing tests. PostgreSQL/S3 tests cannot start without their test environment variables, real
  document-engine tests fail because their pinned engines/fonts are unavailable, and browser tests
  cannot start because Chromium is not installed. The final-image browser scenario was extended
  and syntax-checked but was not executed locally.
- 2026-08-29: PR #100's first ready run exposed a unit branch-coverage gap: all 1,409 tests passed,
  but branch coverage was 89.72%. Added focused negative-path coverage for malformed CSV rows,
  duplicate in-memory users, missing account mutations, empty renewal passwords, stale renewal
  actors, and absent sessions. The exact `pytest -m unit` CI command now passes 1,410 tests with
  90.11% branch coverage and 93.26% combined coverage.
- 2026-08-29: The next ready run passed global branch coverage but exposed that Alembic's normal
  loader did not attribute revision 14 to `coverage.json`, so changed-line coverage failed closed.
  Added direct revision coverage plus in-process SQL upsert and protected-route cases. The unit
  suite now passes 1,412 tests, the migration reports 100% coverage, and changed application-line
  coverage passes at 90.04% (208/231).
- 2026-08-29: Independent review found and reproduced a stale-renewal race plus final-image,
  validation, audit-attribution, and administration-refresh gaps. Renewal now uses compare-and-set
  semantics across memory, SQLite, and PostgreSQL; CSV usernames share the database length/control
  contract; startup and self-service audits identify system/user actors accurately; reset reloads
  the account card; and both final rootless profiles mount, apply, replace, and restart with a real
  provisioning CSV. Regression tests cover every finding.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or
progress changes.
