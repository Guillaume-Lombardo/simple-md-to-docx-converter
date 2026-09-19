---
ticket: T72
linear_id: G1L-540
linear_url: https://linear.app/g1lom/issue/G1L-540/t72-build-the-experimental-revert-workspace
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T72 - Build the experimental Revert workspace

## Objective

Add an experimental Next.js `Revert` workspace for submitting document-to-Markdown jobs and
downloading their results.

## Acceptance criteria

* Add `Revert` to the authenticated application navigation with a clearly visible stamp-style
  `Experimental` treatment associated with the tab label; the state is also conveyed in accessible
  text and never by shape, position, or color alone.
* Build an accessible `/revert` workspace with file selection and drag-and-drop, submission,
  progressive polling/backoff, status, cancellation, expiration, safe errors, and result download.
  Fetch authenticated `GET /api/v1/reversions/capabilities` and derive the supported-format hint,
  file chooser, and bounded client validation from its versioned response. Do not duplicate or
  hardcode the T69 format matrix or fallback constraints; if capabilities are unavailable or an
  unsupported schema version is returned, disable submission and render the safe backend-
  unavailable state. Server validation remains authoritative.
* Call the FastAPI `/api/v1/reversions` routes directly through same-origin relative URLs; do not
  add Next.js business routes, server actions, persistence, authorization, or credential forwarding.
* Preserve stable idempotency reuse after ambiguous transport failures and never automatically
  replay a mutation when the outcome is unknown.
* Clearly state that conversion is local and does not perform OCR; present scanned/image-only PDF
  failures without offering or invoking hosted Firecrawl OCR.
* Describe the workflow as CPU-only and low-compute without making an unmeasured speed or resource
  guarantee.
* Render authenticated/loading/session-expiry/backend-unavailable states consistently with the
  existing production shell.
* Add strict TypeScript, component, transport, accessibility, responsive-layout, browser-behavior,
  and coverage-gate tests without regressing Convert, Templates, or administration workflows.

## Dependencies

* T60
* T61
* T64
* T67
* T71

## Implementation boundary

* Own the Revert navigation entry, experimental visual treatment, `/revert` presentation, typed
  client contract, and frontend tests.
* T67 is a hard dependency. Start only after T67 has merged its own normative package-manager,
  bootstrap, workspace, command, and lockfile decision, then use that established contract without
  redesigning it or migrating the isolated Mermaid toolchain. This ticket does not select pnpm,
  Corepack, or another replacement while the current npm contract remains authoritative.
* Do not duplicate FastAPI validation or business behavior and do not add OCR.
* Do not change the existing Convert workflow except for the shared navigation entry.

## Quality requirements

* Meet the existing CSP, CSRF, cookie-stripping, same-origin, accessibility, rootless-frontend, and
  browser coverage contracts.
* Keep user-facing text and repository artifacts in English.
* Final two-profile rootless browser E2E remains blocking in T73, not waived.

## Progress

* 2026-09-19: Follow-up Zen/Firefox page, upload and download errors were traced to
  Nginx returning 421 HTML before proxying API requests. Markweave's custom HTTPS
  virtual host did not enable HTTP/2, unlike sibling hosts sharing its wildcard
  certificate. A reused HTTP/2 connection with a sibling TLS server name and the
  Markweave request authority reproduced 421. Enabling HTTP/2 only for Markweave
  changed the same probe to 200 JSON; authentication and page APIs passed on that
  connection. Real Firefox tests then passed both conversions and downloads with
  no JavaScript errors. This was a test-deployment configuration correction; no
  application code changed and application suites were not repeated. The earlier
  browser-cache hypothesis was unconfirmed. Metadata-only API diagnostics and the
  reproduction are documented with the deployment runbook.

* 2026-09-19: Final verification records 207 frontend tests passing with 90.32%
  branch coverage, successful real HTTPS browser conversion in both directions,
  and healthy Compose services after the deployment overlay's Host-preserving
  router health-probe correction. The canonical non-engine Python suite reports
  4,217 passed, 6 failed, and 44 errors: PostgreSQL/S3 configuration is unavailable;
  two checkout permission failures and one process-group failure pass on targeted
  rerun. The complete run remains non-green. Application branch coverage is 90.69%
  and changed Python-line coverage is 100%. Ruff, ty, E2E syntax and diff checks pass.

* 2026-09-19: The rebuilt rootless frontend and patched backend pass the real HTTPS
  browser smoke test: login, Markdown-to-DOCX conversion/download, DOCX-to-Markdown
  conversion/download, and output-content verification. Bidirectional navigation leaves the
  destination source empty, transfers no errors, and triggers no upload; exactly one explicit
  request reaches each conversion API. No browser JavaScript errors occur. All Compose
  dependencies and the host-native broker are running. The deployment runbook is retained on
  docker-box alongside the Compose wrapper and overlays. Test broker certificates last 30 days;
  the full two-profile E2E matrix and Red Hat runtime remain unverified locally. No publication
  or merge was performed; T72 remains In Progress until verified on main.

* 2026-09-19: SSH access was restored through the explicitly authorized 1Password SSH
  agent. Provisioned the Compose test instance on docker-box and HTTPS routing on nginx at
  `https://markweave.g1lom.xyz`, including the host-native rootless Podman broker, pinned
  reverse-attempt image, and mutual TLS. The real browser uncovered a separate Revert
  polling defect: calling native `clearTimeout` with the controller as receiver raises
  `Illegal invocation`, leaving completed work displayed as queued. Applied the existing
  Convert controller's receiver-free cancellation pattern to Revert and added polling and
  disposal regressions. All frontend checks pass: 207 tests, 90.32% branch coverage. No
  cross-workspace transfer was reproduced; the original Red Hat message remains unconfirmed.

* 2026-09-19: User authorized a Compose test deployment on `docker-box`, exposed over HTTPS
  through the separate `nginx` host. Both hosts resolve through Tailscale, but ordinary SSH and
  Tailscale SSH fail host-key verification before authentication. No remote state was changed.
  Prepared a local test overlay using the corrected frontend and pinned 0.6.2 backend, plus an
  Nginx HTTPS configuration template; Compose configuration validation passes with placeholder
  inputs. Deployment awaits verified SSH access and the requested public hostname. Actual Revert
  execution also requires inspection/provisioning of the documented host-native rootless Podman
  isolation broker and its exact attempt image; the draft Compose overlay does not enable it.

* 2026-09-19: Confirmed the user's explicit requirement that Convert and Revert are independent
  streams. Added bidirectional component regressions using the actual controllers and workspace
  components: each source is sent only to its matching endpoint with its own idempotency key,
  navigation never submits the other source, and a departed workspace's late upload failure
  cannot enter the current workspace. Extended the final-image browser scenario in the reverse
  direction and documented the independence contract. The existing controllers already satisfy
  these cases; no controller change was necessary. Frontend checks pass all 205 tests with 90.32%
  branch coverage, including formatting, lint, types, bindings, and structure gates. E2E syntax
  and diff checks pass. The final-image scenario remains unexecuted locally; earlier deployment
  and Python-check limitations remain unchanged.

* 2026-09-19: Reopened for the reported 0.6.2 error on an empty Revert page after
  selecting Markdown in Convert. A production build with sandboxed Chromium and stubbed API
  responses did not reproduce cross-workspace file transfer. The default Compose deployment
  leaves the reverse service unconfigured, which renders an ambiguous supported-types loading
  error. Clarify that unavailable-service state and cover empty-state/navigation isolation.
  The exact deployed error remains unconfirmed because its text is not yet available.
* 2026-09-19: Clarified the unavailable-service message and documented the default 0.6.2
  configuration. Component tests verify the initial empty/error-free Revert workspace; the
  final-image browser scenario now selects Markdown in Convert before entering Revert and checks
  source isolation and absence of unintended submissions. Frontend checks pass (203 tests,
  90.16% branch coverage), as do the 16 root browser-helper tests, production build, diff check,
  and E2E JavaScript syntax check. Sandboxed Chromium against the production build with stubbed
  API responses verifies navigation isolation and the corrected message; this is not real-backend
  or final-image E2E evidence. The extended two-profile final-image test was not run locally.
  Python canonical checks were not run because no Python/backend files changed. Red Hat runtime
  verification and the exact reported message remain unavailable. No publication or deployment
  was performed; the ticket remains In Progress pending review and verification on main.

* 2026-09-03: Created from the approved feasibility decomposition; blocked by T71.
* 2026-09-03: User-facing scope now communicates CPU-only, low-compute processing without an
  unsupported performance claim.
* 2026-09-06: Started from verified main `156f2e644f3d6da8d6a43230e514705f12924fd4`
  after T71 reached Done in the repository and Linear. The implementation will reuse the existing
  generated OpenAPI/Valibot bindings and same-origin transport, keep capabilities authoritative,
  and confine changes to the Revert workspace, shared navigation, and frontend tests.
* 2026-09-06: Implemented the authenticated experimental workspace, authoritative capability
  admission, stable ambiguous-retry idempotency, polling, cancellation, safe result download,
  responsive presentation, and browser coverage. Frontend checks, 203 Vitest tests, the production
  build, four production-server tests, workspace validation, Ruff, and ty pass locally; frontend
  branch coverage is 90.16%. The canonical Python suite was attempted: all locally runnable tests
  passed after moving the generated pnpm bootstrap cache outside the scanned tree, while the tests
  explicitly marked for PostgreSQL and S3 failed because those services were not available. The
  final-image browser run could not complete locally because the shared Podman image store exhausted
  the VM filesystem while committing the frontend image. No unrelated global images or volumes were
  removed; the hosted exact-head CI remained the blocking browser validation, while T73 retains the
  complete successful two-profile final-image matrix.
* 2026-09-06: PR #214 passed exact-head CI, including frontend and both rootless E2E profiles, and
  CodeRabbit confirmed all three findings addressed with no unresolved review threads. It was
  squash-merged as `2e54bdc3fa9a2125c75d7492ec7459f0f4bdbbb3`; exact-main CI run `34043478649`
  then passed its light, frontend, standalone E2E, distributed E2E, and final gate jobs. T72 is
  complete on verified `main`; the full successful cross-format release matrix remains with T73.

- 2026-09-19: Follow-up fix verified on main `5e789c11600d429997c037b4d24eea399821a2ed`
  through squash PR #232. All required exact-head and main CI domains pass (runs 35453205924
  and 35454910111). Release 0.6.3 succeeds in run 35454910249. This completes the reopened
  corrective work; T73 final reverse-engine qualification remains separate.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
