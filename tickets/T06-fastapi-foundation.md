---
ticket: T06
linear_id: G1L-316
linear_url: https://linear.app/g1lom/issue/G1L-316/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T06 - Build the FastAPI foundation and local authentication

## Objective

Build FastAPI foundations, configuration, English errors, local accounts, sessions, authorization abstraction, and health endpoints.

## Acceptance criteria

- The implementation satisfies the T06 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- T01
- T05

## Progress

- 2026-08-23: Started implementation after confirming title, project, team, priority, scope,
  acceptance criteria, and dependency parity with Linear G1L-316. T01 and T05 are `Done`; T06 has
  no remaining dependency blocker. Work is isolated on `feat/T06-fastapi-auth-foundation` from
  `main` at `db698a5`.
- 2026-08-23: Recorded the project-manager decisions that close the authentication-policy blocker:
  there is no public registration; an initial administrator is injected from configuration/secrets;
  only administrators create, deactivate, reactivate, or reset local accounts; Argon2id defaults to
  `m=19456 KiB`, `t=2`, `p=1` and remains configurable; opaque CSPRNG session tokens contain at
  least 128 bits; idle and absolute lifetimes default to 30 minutes and 8 hours and remain
  configurable; logout and administrative actions revoke sessions server-side; and the session
  cookie is `HttpOnly`, `Secure`, and `SameSite`.
- 2026-08-23: The project manager explicitly approved deferring final-image rootless E2E coverage
  for the T06 login/session/administration workflow to T20/T21. T06 must still provide unit,
  functional ASGI, and real Argon2id/HTTP integration coverage. The durable debt is to repeat the
  primary path and relevant authentication, authorization, revocation, and expiration failures
  against the hardened rootless image once it exists; the image, browser/runtime packaging, and
  both-profile deployment boundaries do not exist before T20/T21.
- 2026-08-23: Implemented the FastAPI/Uvicorn factory, fail-closed typed configuration, stable English
  errors, atomic administrator bootstrap, Unicode-normalized local accounts, Argon2id hashing and
  successful-login rehash, opaque digest-backed sessions, idle/absolute expiration, session
  rotation and revocation, session-bound CSRF, administrator authorization, health probes, minimal
  login HTML, OpenAPI, and storage/readiness/security ports. T06 deliberately supplies temporary
  thread-safe memory adapters; persistence and the across-process no-reset guarantee remain T12.
- 2026-08-23: Activated the hosted `functional` CI domain with its ASGI and real Argon2id/HTTP
  integration suites. All 99 default and full tests pass with 97.58% total application coverage
  (98% rounded; 56 branches measured) and 98.41% changed-line coverage; the 11-test domain command
  passes without skips. Ruff
  formatting/linting, `ty`, locked `uv` synchronization, the local CI validator, actionlint with
  ShellCheck, and `git diff --check` pass. The only warning is Starlette's non-blocking TestClient
  notice about its future `httpx2` transition. Final-image rootless E2E remains the explicitly
  approved T20/T21 debt documented in `docs/authentication.md`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
