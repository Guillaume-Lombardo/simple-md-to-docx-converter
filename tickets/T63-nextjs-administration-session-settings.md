---
ticket: T63
linear_id: G1L-527
linear_url: https://linear.app/g1lom/issue/G1L-527/t63-migrate-administration-and-session-settings-to-nextjs
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T63 - Migrate administration and session settings to Next.js

## Objective

Migrate template, user, and session-policy administration to the Next.js frontend with complete owner and administrator parity.

## Acceptance criteria

* Support template search/filtering, owner display, creation, download, metadata update, replacement, version history, copy-forward restore, archive/delete guards, preferred selection, and system fallback behavior.
* Preserve owner/administrator authorization, immutable ownership, ETag/If-Match concurrency, safe DOCX validation failures, upload bounds, audit behavior, and response-generation fencing.
* Preserve replacement editing and explicit clearing of comma-separated expected fonts: trimmed non-empty values remain ordered, while blank input sends the explicit empty field rather than omitting it; cover both paths in component, browser, and final-image tests.
* Support administrator user search, creation, activation, deactivation, password reset, and required-password-renewal controls with duplicate-submit and revoked-session handling.
* Add an administrator-only control for the effective system-wide inactivity duration delivered by T59, showing the default/effective value, approved bounds, absolute ceiling, current revision, validation errors, stale-write conflicts, and a clear warning that tightening can require current users to sign in again.
* Never let frontend validation, cached data, or hidden controls replace FastAPI authorization and policy enforcement.
* Meet accessibility requirements for keyboard operation, focus restoration, forms, confirmation dialogs, lists/tables, live errors/notices, responsive layout, and supported browser behavior.
* Add unit/component, contract, integration, real-browser, and final rootless-image E2E coverage with two regular users and one administrator across both profiles, including stale ETags, forbidden actions, revoked sessions, persisted policy changes, and restart recovery.
* Keep the legacy administration page available until T64 completes the verified cutover.

## Dependencies

* T59
* T61
* T17
* T65
* T66

## Implementation boundary

* Own Next.js template, user, and session-policy administration pages and their frontend tests.
* Backend session-policy behavior belongs to T59.

## Quality requirements

* Preserve FastAPI as the sole business, authentication, authorization, persistence, and job-processing backend.
* Add automated tests for every introduced behavior and keep the applicable frontend and Python coverage gates.
* Cover every affected real boundary with integration tests and every delivered browser workflow with final rootless-image E2E tests for both storage profiles.
* Keep repository artifacts and user-facing text in English.
* Run all applicable canonical formatting, linting, type-checking, contract, browser, Python, container, and E2E checks.

## Progress

* 2026-09-01: Created with the administrator inactivity-duration control explicitly dependent on the FastAPI-owned T59 policy.
* 2026-09-02: Blocked on T65 for authoritative preferred/fallback template identifiers, template upload limits, and the operator-configured absolute session ceiling required by the administration UI.
* 2026-09-02: Started from verified `main` at `634d3fe6112f9d2040a708b0acc9599b509f2d78` after T65 completion; implementing the presentation-only Next.js administration workflows with FastAPI remaining authoritative.
* 2026-09-02: Template, user, and navigation work continues while the session-policy control waits for T66 to expose authoritative role-specific minimum, default, and maximum metadata; T63 remains In Progress and does not duplicate those backend values.
* 2026-09-02: T66 landed on verified `main` at `27b166adb938f791e1ac4dba06c61dc25775c546`; session-policy implementation resumed using only its generated authoritative metadata while shared final-image harness integration remains pending T62.
* 2026-09-02: Implemented the metadata-driven administrator session-policy workspace with required ETag/CSRF, atomic role updates, authoritative refresh, dynamic bounds and absolute-ceiling validation, stale-write no-replay behavior, session-expiry handling, accessible navigation, and dedicated unit/component/browser coverage. Frontend gates, the production build/runtime tests, focused backend policy tests, and documentation/CI-selection tests pass; both-profile final-image invocation and checkpoint environment wiring remain pending T62's shared-harness merge.
* 2026-09-02: Merged verified T62 `main` at `a9f781766de255e807679efef86b15135b7f3cc0` without conflicts and completed the shared-harness integration. Each storage-profile run now derives exact user/admin durations and revision from the already restored and verified T59 checkpoint, passes them to the dedicated administration browser journey, and runs it after the ordinary authentication/conversion phase without weakening T62's isolated admission, restart, or expiry phases. Frontend gates/build/runtime, Ruff, ty, focused policy/OpenAPI/documentation/CI/harness tests, ShellCheck with established baseline exclusions, and Bash/Node syntax pass; hosted both-profile execution remains to be observed before completion.
* 2026-09-02: Addressed independent review findings: stale template writes now retain the response fence while loading a fresh authoritative snapshot, reset every editable field, and require an explicit non-replayed retry; current and historical downloads now use the authenticated same-origin transport, validate response metadata, preserve the server filename, revoke blob URLs safely, and expire the shell on an authoritative 401. Component and final-image browser coverage exercises both download variants, exact stale refresh values, and one-request session expiry. Frontend gates/build/runtime and the relevant Python, harness, OpenAPI, documentation, Ruff, and ty checks pass; hosted both-profile execution remains to be observed.
* 2026-09-02: Addressed PR review feedback by preserving complete CSRF cookie values in the administration browser client and stabilizing the latest session-expiry callback across all three administration workspaces. Parent rerenders no longer abort, restart, or strand template, account, or session-policy requests; focused regressions cover in-flight policy completion and both list loads. Frontend gates/build/runtime and the relevant browser, Python harness, OpenAPI, Ruff, and ty checks pass; hosted both-profile execution remains to be observed.
* 2026-09-02: Wired the focused administration CSRF-cookie regression into the final-image harness once per storage profile, immediately before the unchanged single administration journey. Harness tests lock the exact invocation count and phase order so hosted CI cannot silently omit or duplicate either test.
* 2026-09-02: Hosted exact-head run `33654775170` reached the real administration journey in both profiles and failed identically because Playwright's substring accessible-name matching made the `Username` field collide with the simultaneously visible `Search by username` field. The administration E2E now uses exact accessible-name matching for every `Username` field while preserving strict mode and the unchanged once-per-profile journey.
* 2026-09-02: Hosted rerun `33656386303` exposed the same Playwright substring behavior for `Temporary password` versus `New temporary password` in both profiles. A systematic manual audit added exact accessible-name matching to every literal `getByRole` and `getByLabel` selector in the administration journey while retaining intentional regex matching. The canonical source regression is deliberately limited to exact call counts and options for the two concurrently rendered collision pairs (`Username`/`Search by username` and `Temporary password`/`New temporary password`) and rejects `.first()`/`.nth()` strict-mode bypasses; it does not attempt to parse JavaScript. The focused administration component suite (20 tests), static/harness suite (12 tests), cookie regression (2 tests), frontend structure suite (8 tests), Node/Bash syntax, Ruff, Prettier, and diff checks pass locally.
* 2026-09-02: Hosted rerun `33658584456` reached template download in both profiles and exposed a missing `Cache-Control` response header. The shared backend response for current and historical template content now returns the specified exact `private, no-store` value while preserving the DOCX media type, safe disposition, immutable ETag, and `nosniff`; the additive OpenAPI response-header contract and both route variants are covered directly. Focused backend/OpenAPI/harness tests pass (34 tests), focused frontend download tests pass (41 tests), OpenAPI compatibility reports only six compatible header additions, and binding freshness, Node/Bash syntax, Ruff, ty, Prettier, and diff checks pass locally.
* 2026-09-02: Hosted rerun `33660468452` reached account creation in both profiles and proved a client lifecycle race: the success notice became visible before the authoritative account reload, allowing later form input that the first handler then reset. User and template creation now retain their mutation fence and disabled pending state through the authoritative reload, synchronously reset only after that reload succeeds, and publish success afterward. Deferred-reload component regressions preserve submitted values while pending, prohibit interaction and duplicate submission, then verify reset-before-success for both forms. The complete frontend check passes with 178 tests and 95.33% statements/90.12% branches/96.97% functions/98.34% lines; production build/runtime, focused backend/OpenAPI/harness tests (34), Ruff, ty, binding freshness, Node/Bash syntax, and diff checks pass.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
