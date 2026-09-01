---
ticket: T60
linear_id: G1L-524
linear_url: https://linear.app/g1lom/issue/G1L-524/t60-build-the-web-nextjs-typescript-and-tailwind-foundation
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T60 - Build the web Next.js TypeScript and Tailwind foundation

## Objective

Create the production-ready Next.js, TypeScript, and Tailwind CSS application foundation under `web/` without changing user workflows.

## Acceptance criteria

* Create an isolated `web/` application using the architecture, runtime, package-manager, and version policies approved by T58.
* Enable strict TypeScript, linting, formatting, deterministic lockfile installation, reproducible production builds, and Tailwind CSS with a small accessible design-token foundation.
* Generate typed frontend bindings for the production runtime and test fixtures from the canonical OpenAPI contract; fail CI when generated contract artifacts are stale, and do not hand-maintain a divergent API model.
* Provide one typed API transport for JSON, multipart uploads, downloads, error envelopes, ETags, CSRF headers, idempotency keys, cancellation, and request aborts.
* Establish accessible application-shell, form, alert, loading, progress, dialog, table/list, and navigation primitives without introducing a separate business backend.
* Implement the CSP interception hook as `web/proxy.ts` with the named `export function proxy`, never deprecated `middleware.ts` or an exported function named `middleware`; block on structural and production-build proof. On exact Next.js `16.3.4` production dynamic rendering, enforce the reviewed nonce CSP without `unsafe-inline` or `unsafe-eval`; prove every dynamically rendered HTML document response receives a fresh nonce, cached HTML never reuses one, `script-src` and `style-src` contain that same nonce, every framework bootstrap script and inline style element carries it, no inline style attribute or unnonced inline style is emitted, and no generated page or asset requires eval. Prove content-hashed `/_next/static/**` assets retain stable immutable caching without a generated nonce CSP, and non-HTML/content-free responses—including empty header-overflow `431` and saturation/draining `503` responses—also receive no generated nonce CSP. This gate blocks T60 and is repeated against the final image in T64; there is no framework exception.
* Implement the supported `web/server.mjs` custom production server without Next.js `output: "standalone"`; enforce 16 KiB request headers with a zero-length overflow `431`, the exact 128/129 per-replica admission boundary, zero-length saturation/draining `503` responses, exact finish/close accounting, and bounded 30-second SIGTERM draining with no API proxying.
* Add blocking routing-fixture tests proving that every frontend route and method strips the upstream `Cookie` header and all downstream frontend `Set-Cookie` fields, covering a named-page GET, framework-asset GET, unknown-path GET, POST to a named page, PATCH to an unknown catch-all path, and multiple response fields; prove both directions survive unchanged for exact `/api/v1`, an `/api/v1/**` descendant, and a representative direct FastAPI operational route.
* Add frontend unit/component coverage thresholds at least as strict as the existing JavaScript 90% line, branch, and function gates.
* Integrate deterministic frontend dependency, build, type, lint, test, cache, and affected-path selection into CI.
* Add a minimal rootless production smoke test for the frontend runtime and its two internal health endpoints on the Service-only probe port, including normalized public-router denial of the complete `/_frontend/health/**` prefix and decoded/case-varied equivalents before the catch-all.
* Leave the current FastAPI-rendered pages as the production UI until the cutover ticket.

## Dependencies

* T58
* T45

## Implementation boundary

* Own `web/`, shared UI primitives, generated API bindings, frontend CI, and the frontend runtime smoke test.
* Do not migrate complete login, conversion, template, or account workflows in this ticket.

## Quality requirements

* Preserve FastAPI as the sole business, authentication, authorization, persistence, and job-processing backend.
* Add automated tests for every introduced behavior and keep the applicable frontend and Python coverage gates.
* Cover every affected real boundary with integration tests and every delivered browser workflow with final rootless-image E2E tests for both storage profiles.
* Keep repository artifacts and user-facing text in English.
* Run all applicable canonical formatting, linting, type-checking, contract, browser, Python, container, and E2E checks.

## Progress

* 2026-09-01: Created as the frontend foundation after T58; it deliberately leaves the existing production pages active.
* 2026-09-01: Review clarified that production runtime code must use generated typed bindings; fixtures are generated from the same OpenAPI contract for tests only.
* 2026-09-01: Implementation started from verified T58 completion on exact `main` at `15b5620439488392e550eeda036d76d0be414e69`. The work is bounded to the isolated `web/` foundation, generated bindings, shared primitives, custom frontend server, routing/CSP/runtime tests, CI integration, and rootless smoke proof; existing FastAPI-rendered production pages remain active.
* 2026-09-01: Implemented the isolated Next.js 16.3.4, TypeScript 6.0.3, and Tailwind CSS 4.3.3 foundation with exact npm locking, generated production and fixture OpenAPI types, one typed same-origin transport, accessible shared primitives, strict per-request nonce CSP, and the bounded two-port custom server. CI now caches and validates the frontend independently and selects the rootless frontend domain for `web/**` or OpenAPI changes. Production routing and the FastAPI-rendered interface are unchanged.
* 2026-09-01: Local frontend verification passed formatting, lint, strict types, generated-binding freshness, zero-vulnerability `npm audit`, 4 structural tests, 37 unit/component/runtime tests with 98.29% statements, 94.04% branches, 97.43% functions, and 99.04% lines, the optimized production build, 3 production nonce-CSP/asset/probe tests, and the digest-pinned arbitrary-UID read-only-root Podman smoke test. The CI selector and workflow policy passed 239 focused Python tests. ESLint remains exact 9.39.2 because the reviewed Next.js 16.3.4 plugin stack fails under ESLint 10; dependency updates remain separately reviewed and lockfile-pinned.
* 2026-09-01: Repository-wide `uv sync`, Ruff, `ty`, legacy JavaScript coverage, OpenAPI freshness, and CI-policy validation passed. The canonical default Python run reached 2,160 passes and 95.48% application coverage but could not run 32 PostgreSQL cases or 3 RustFS cases without their configured services. The complete run reached 2,166 passes and 95.50% coverage; its remaining 41 engine/storage failures and 32 PostgreSQL setup errors are the expected local gaps because Pandoc, Mermaid/Chromium, LibreOffice, the approved fonts, PostgreSQL, and RustFS are unavailable. The T60-owned rootless frontend boundary is independently verified and does not depend on those backend services or document engines.
* 2026-09-01: Review corrections narrowed nonce-CSP exclusions to real framework asset prefixes and added production proofs for named, not-found, extension-like, and global-error HTML. Generated Valibot schemas now validate successful and error JSON at runtime; malformed payloads map to the fixed `UNEXPECTED_RESPONSE`. Real HTTP fixtures verify routing credential isolation, admission limits, draining, synchronous failures, and bounded shutdown. Readiness now fingerprints required route manifests and every immutable asset, and the reusable native modal dialog owns unique labels, Escape handling, close behavior, and focus restoration. Frontend checks pass with 98.32% statements, 90.72% branches, 98.11% functions, and 100% lines; the optimized production build, production nonce/error tests, and rebuilt arbitrary-UID rootless smoke image also pass.
* 2026-09-01: Second-review corrections make CSP document-intent-aware: absent favicon and every HTML error receive fresh nonces, while RSC, prefetch, JSON, empty, HEAD, static, and custom-server failures receive none. The rootless smoke now puts the real host-validating routing fixture in front of the final container and proves content-free denial for exact, descendant, encoded, case-varied, repeated-slash, and dot-normalized frontend probe paths. The fixture preserves reviewed Host, Origin, Cookie, and Set-Cookie fields only where required, drops untrusted forwarding headers, rejects unknown hosts, and includes `/docs/**`. Readiness resolves route and build-manifest references before hashing; typed transport accepts generated void 204 responses, parses exact JSON media types, and rejects API lookalike paths. All frontend, production, rootless, CI-policy, Python static, and legacy JavaScript checks pass.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
