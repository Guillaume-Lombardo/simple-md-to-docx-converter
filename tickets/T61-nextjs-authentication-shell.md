---
ticket: T61
linear_id: G1L-525
linear_url: https://linear.app/g1lom/issue/G1L-525/t61-migrate-authentication-and-the-application-shell-to-nextjs
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T61 - Migrate authentication and the application shell to Next.js

## Objective

Migrate the login, logout, password-renewal, authenticated application shell, and session-expiry experience to the Next.js frontend while FastAPI remains the authentication authority.

## Acceptance criteria

* Implement login, logout, current-user loading, protected navigation, required-password-renewal, and post-renewal reauthentication with accessible English states.
* Preserve FastAPI's opaque session cookie, separate CSRF cookie/header, exact Origin validation, authentication-version checks, and server-side revocation; never expose the HttpOnly token to Next.js or browser JavaScript.
* Keep browser API requests same-origin and prevent open redirects, credential reflection, cacheable authenticated HTML/data, and server-side request forgery through user-controlled destinations.
* Handle idle timeout, absolute timeout, revoked sessions, backend unavailability, malformed responses, duplicate submits, late responses, and navigation races deterministically.
* Display the effective inactivity duration where the user needs it and require a fresh login after FastAPI reports expiry; do not implement a client-only timeout as the security boundary.
* Preserve restricted password-renewal sessions so they cannot reach conversion, template, account, audit, or session-policy operations.
* Add unit/component, real HTTP integration, real-browser, and final-image E2E coverage for two users and one administrator across both profiles.
* Keep the legacy authentication pages available until T64 completes the verified cutover.

## Dependencies

* T59
* T60
* T30

## Implementation boundary

* Own Next.js authentication pages, protected layout, session-expiry UX, and browser authentication tests.
* Backend session-policy implementation belongs to T59.

## Quality requirements

* Preserve FastAPI as the sole business, authentication, authorization, persistence, and job-processing backend.
* Add automated tests for every introduced behavior and keep the applicable frontend and Python coverage gates.
* Cover every affected real boundary with integration tests and every delivered browser workflow with final rootless-image E2E tests for both storage profiles.
* Keep repository artifacts and user-facing text in English.
* Run all applicable canonical formatting, linting, type-checking, contract, browser, Python, container, and E2E checks.

## Progress

* 2026-09-01: Created after the backend session policy and frontend foundation tickets were defined.
* 2026-09-02: Implementation started from verified main `79088b01`. Added the additive
  role-effective inactivity value to session/login responses and implemented the unpublished
  Next.js authentication state machine, accessible login and renewal pages, protected shell,
  fixed navigation, safe expiry behavior, generated bindings, and focused frontend/backend tests.
* 2026-09-02: Implementation is complete pending review. The root-scoped controller now fences
  duplicate, aborted, late, and navigation-racing requests without timers or polling, keeps the
  opaque session token outside JavaScript, and handles restricted renewal and authoritative
  expiry without replay. Final rootless-image workflows passed for standalone SQLite and
  distributed PostgreSQL with an administrator and two users, including restart persistence,
  renewal, deactivation, and absolute expiry. Frontend, OpenAPI, Python unit, lint, and type checks
  also passed; the canonical all-domain pytest invocation requires separately configured
  PostgreSQL and RustFS test endpoints, which were unavailable outside the self-cleaning E2E
  harness.
* 2026-09-02: Independent-review corrections made login progress visibly announced and disabled
  duplicate submission while preserving controller fencing and deterministic abort/navigation
  supersession. The isolated two-second absolute-lifetime phase now routes the final Next.js image
  to the final FastAPI image and uses Chromium to prove one post-expiry authenticated request gets
  an authoritative `401`, reaches the fixed sign-in-again state, and is not replayed. The corrected
  final-image workflow passed for both standalone SQLite and distributed PostgreSQL.
* 2026-09-02: Completed after implementation PR #167 merged exact approved head
  `8c3c75e1cb91e7e79eeacaf3c44032a877400d78` as `main` commit
  `532320956557e87adf369cf0cd65718008708d06`. Exact-head CI `33576979482` and exact-main CI
  `33577754390` passed all 11 jobs and the final gate, including frontend, functional,
  document-engine, standalone/distributed storage, and standalone/distributed final-image E2E.
  Independent exact-revision review approved, both CodeRabbit findings were corrected, and all
  review threads were resolved. Final evidence includes 1,979 passing unit tests, 94.32% total
  coverage, 90.29% application branch coverage, 100% changed Python-line coverage, 75 frontend
  tests above 91% branch coverage, and real Chromium authentication/expiry workflows for two users
  and one administrator in both storage profiles.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
