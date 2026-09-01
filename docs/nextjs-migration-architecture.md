# Next.js migration architecture

This document is the reviewed T58 contract for migrating Markweave's browser interface from
FastAPI-rendered HTML and native JavaScript to Next.js, TypeScript, and Tailwind CSS. The
[product specification](product-specification.md) remains normative. This guide makes its topology,
security, runtime, release, cutover, and parity decisions actionable for T59–T64. It does not change
the currently deployed interface.

## Decision record

The decision was reviewed against upstream support information on 2026-09-01:

- [Node.js release policy](https://nodejs.org/en/about/previous-releases) says production must use
  an Active or Maintenance LTS line. Node.js 24 is LTS.
- [Next.js support policy](https://nextjs.org/support-policy) identifies Next.js 16 as Active LTS
  and rejects canary releases for production.
- The [Next.js 16 upgrade guide](https://nextjs.org/docs/app/guides/upgrading/version-16)
  deprecates `middleware.ts` and the `middleware` export in favor of `proxy.ts` and a function named
  `proxy`; the [official CSP guide](https://nextjs.org/docs/app/guides/content-security-policy) uses
  that convention for per-request nonces.
- [TypeScript 7.0's release announcement](https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/)
  says 7.0 has no programmatic API and recommends the TypeScript 6 compatibility package for tools
  that still need one. The migration therefore stays on the latest reviewed 6.0 patch until the
  complete Next.js, lint, editor, binding-generation, and test toolchain validates a later line.
- [Tailwind CSS releases](https://tailwindcss.com/blog) identify 4.3 as the current stable line.
- Exact [Next.js 16.3.4](https://www.npmjs.com/package/next/v/16.3.4),
  [TypeScript 6.0.3](https://www.npmjs.com/package/typescript/v/6.0.3), and
  [Tailwind CSS 4.3.3](https://www.npmjs.com/package/tailwindcss/v/4.3.3) package metadata and
  registry integrity values come from the public npm registry and are committed through
  `web/package-lock.json`; tags such as `latest`, ranges, and unpinned Git URLs are not production
  inputs.

The initial implementation baseline is:

| Component | Exact baseline | Policy |
| --- | --- | --- |
| Node.js | `24.19.0` | Supplied by both approved UBI images; the build fails if `node --version` differs. Remain on Node.js 24 LTS until a reviewed upgrade. |
| npm | `11.17.0` | The only package manager. Record `packageManager: npm@11.17.0`; the build fails if `npm --version` differs. |
| Next.js | `16.3.4` | Exact Active-LTS patch, never `canary`, `rc`, or a range. |
| TypeScript | `6.0.3` | Exact stable compatibility release; reassess TypeScript 7 only after its API/tooling boundary is compatible. |
| Tailwind CSS | `4.3.3` | Exact stable patch; compile styles at build time with no runtime CDN. |

Every direct dependency, including React and React DOM, uses an exact version. `npm ci
--ignore-scripts` consumes `web/package-lock.json` in CI and container builds; lockfile drift,
install-script requirements, or an unexpected resolved graph fails the build. Production contains
the `.next` production build, public assets, the reviewed custom server, and the exact pruned
production dependency graph. It does not use Next.js `output: "standalone"`, because the
[official custom-server guide](https://nextjs.org/docs/app/guides/custom-server) says that output
mode and a custom server cannot be combined. A dependency update is one reviewed
pull request that refreshes the lockfile, generated bindings when affected, licenses, SBOMs,
vulnerability results, deterministic build evidence, browser tests, rootless smoke tests, and
rollback evidence. Security fixes may be expedited but never floated at startup or build time.

## Production topology and ownership

Production uses two independently hardened application images behind one operator-owned TLS routing
boundary:

```text
browser -- HTTPS --> same-origin router
                       |-- browser pages and /_next/* --> Next.js frontend
                       `-- /api/v1, /api/v1/**, operations --> FastAPI --> workers/storage
```

The frontend is a presentation process. It owns page composition, browser assets, accessible client
state, and rendering only. Browser JavaScript calls FastAPI directly through same-origin relative
`/api/v1` URLs. The frontend has no database, object-store, scanner, worker, or document-engine
credentials; it does not mount `/data` or `/work`; and it has no network route to those services.
Next.js route handlers, Server Actions, Proxy, rewrites, and server components must not proxy API
calls or implement authentication, authorization, validation, quotas, persistence, conversion,
template, account, audit, public service health/readiness, metrics, or OpenAPI behavior. The only
route-handler exception is the two non-public, process-local frontend probes defined below.
`web/proxy.ts` with the named `export function proxy` may generate page CSP nonces and perform
presentation-only routing. The deprecated `middleware.ts` file and `middleware` export are absent.

FastAPI remains the only authority for all business and security decisions. The standalone profile
has one backend `serve` replica with its embedded worker plus one frontend replica. The distributed
profile scales frontend and backend API replicas independently and continues to run separate
external workers. A frontend outage cannot mutate or corrupt backend state; direct API and CLI use
remains available. A backend outage leaves the page process live but browser operations fail with
bounded, safe, retryable presentation states.

The router is infrastructure, not an application backend. It terminates TLS, selects an upstream by
literal path prefix, preserves the original `Host` and `Origin`, rejects unknown hosts, and does not
derive security decisions from `Forwarded` or `X-Forwarded-*`. FastAPI continues to use the exact
operator-set `MARKWEAVE_PUBLIC_ORIGIN`. Next.js receives the same public origin as configuration for
links only; it may not override or infer FastAPI's security origin.

Because same-origin cookies use `Path=/`, the router removes the complete `Cookie` header before
forwarding every request whose selected upstream is the frontend, regardless of HTTP method. This
includes named pages, `/_next/**` assets, and the unknown-path catch-all. It also removes every
`Set-Cookie` field, including multiple header fields, from every frontend response regardless of
method, status, or content type before returning it to the browser. It performs neither
transformation on exact `/api/v1`, `/api/v1/**`, or public operational routes: FastAPI receives the
original `Cookie` header and the browser receives all of FastAPI's `Set-Cookie` fields unchanged.
Thus Next.js cannot consume, overwrite, clear, or mint either the session or CSRF cookie.

## One-origin routing contract

At T64 cutover the router applies this ordered, non-overlapping table:

| Public path | Owner | Rules |
| --- | --- | --- |
| exact `/api/v1` and `/api/v1/**` | FastAPI | No Next.js rewrite, proxy, cache, or body inspection. Preserve incoming `Cookie` and every outgoing `Set-Cookie` field unchanged. This includes uploads and downloads. |
| `/health/live`, `/health/ready`, `/metrics`, `/docs`, `/docs/**`, `/redoc`, `/openapi.json` | FastAPI | Preserve the existing public operational contract and FastAPI response semantics. |
| `/login`, `/change-password`, `/convert`, `/templates`, `/` | Next.js | Strip the complete request `Cookie` header and every response `Set-Cookie` field. `/` preserves the redirect to `/convert`; page behavior follows the parity inventory below. |
| `/_frontend/health` and `/_frontend/health/**` | Public router denial | Return a content-free `404` before any Next.js catch-all; reject decoded or case-varied equivalents rather than forwarding them. Platform probes reach only the two exact internal paths on the frontend Service's separate probe port. |
| `/_next/**` | Next.js | Strip the complete request `Cookie` header and every response `Set-Cookie` field. Only content-hashed framework assets are public and immutable. Source maps are not published. |
| `/static/**` | FastAPI before cutover; absent after legacy removal | Never alias this prefix to new assets. It allows pre-cutover and rollback routes to remain unambiguous. |
| any other path | Next.js | Strip the complete request `Cookie` header and every response `Set-Cookie` field. Return the reviewed accessible `404`; never fall through to FastAPI or an external URL. |

Routing is exact: encoded or case-varied paths do not bypass prefix selection, dot segments are
rejected or normalized once before routing, and no user-controlled header chooses an upstream.
There is no CORS mode and no second browser-visible hostname. Production browser tests fail if any
page, API request, asset, upload, or download crosses origin.

T60 routing-contract tests send sentinel session, CSRF, and unrelated cookies to a named-page GET,
a framework-asset GET, an unknown-path GET, a POST to a named page, and a PATCH to an unknown
catch-all path. They capture every frontend upstream request and require the `Cookie` header to be
absent, then inject
multiple frontend `Set-Cookie` fields across those route/method classes and require all of them to
be absent at the client. The same fixture requires both headers to survive unchanged in both
directions for exact `/api/v1`, an `/api/v1/**` descendant, and a representative public operational
route. These are blocking gates; T64 repeats them through the production router against the exact
final image pair.

During T60–T63, production continues to route all browser paths and `/static/**` to FastAPI. The
unpublished frontend is exercised only through test-only routing that cannot receive production
traffic. T64 changes the routing table once, after every gate in the cutover section passes.

## Runtime image and rootless boundary

The frontend uses a reproducible multi-stage UBI 9 build for the currently supported Linux/AMD64
release target. The repositories are Red Hat's supported
[UBI 9 Node.js 24](https://catalog.redhat.com/en/software/containers/ubi9/nodejs-24/67f623722bea78d47b14c671)
and [minimal runtime](https://catalog.redhat.com/en/software/containers/ubi9/nodejs-24-minimal/67f62622a7637197c48f87ab):

- builder:
  `registry.access.redhat.com/ubi9/nodejs-24@sha256:2bae1ec6e0e4892583459f1709426fb6bfa67ed2fa2b4b974645b644d10e9693`;
- runtime:
  `registry.access.redhat.com/ubi9/nodejs-24-minimal@sha256:ec0fcd4a3b6c64a1b9b1571c9528172508471f8a295f857215057a140aa5f2b4`.

These are the reviewed AMD64 image digests observed on 2026-09-01 and both resolve to Node.js
`24.19.0` and npm `11.17.0`. A refresh must update the digest and expected tool versions together
and repeat supply-chain and runtime verification. The runtime copies only the production `.next`
build output, exact pruned production `node_modules`, public assets, required notices, and
`web/server.mjs`. The reviewed custom server uses the supported Next.js production server API; it
must not be packaged with `output: "standalone"`. The image contains no compiler, package cache,
source tree, development dependency, shell-based package install, or dynamically downloaded asset.

The process listens for page traffic on unprivileged port 3000 and for Service-internal probes on
unprivileged port 3001, and runs as an arbitrary non-zero UID in group 0. It
requires a read-only root filesystem, `no-new-privileges`, an empty capability set, RuntimeDefault
seccomp, no privilege escalation, and no service-account token. The only writable mount is a
memory-backed `/tmp`; `HOME`, npm cache, and Next.js runtime caches are disabled or point into that
bounded mount. The application must run with no writable current directory. It receives SIGTERM,
stops accepting new requests, and exits within the platform grace period without writing durable
state.

The initial per-replica production budget is explicit and is a required T64 deployment contract:

| Resource | Request | Limit |
| --- | ---: | ---: |
| CPU | `100m` | `500m` |
| memory | `128Mi` | `256Mi` |
| ephemeral storage | `32Mi` | `64Mi` |
| `/tmp` memory volume | — | `32Mi` |
| Node old-space heap | — | `160Mi` |
| process count | — | `64` |
| in-flight HTTP requests per replica | — | `128` |
| request-header bytes | — | `16KiB` |
| graceful shutdown | — | `30s` |

The frontend accepts no API upload body, so these values do not change T18's document or worker
budgets. Operators may lower request reservations after measurement. Raising a limit or changing a
hard ceiling requires load evidence and a reviewed deployment change; it is never inferred from a
framework default.

`web/server.mjs`, owned by T60, is the enforcement point for the per-replica HTTP ceilings. It uses
Node's HTTP server with `maxHeaderSize: 16384`, returns a zero-length `431` from its `clientError`
handler for header overflow, counts admitted requests across page and asset handlers, admits no
more than 128 simultaneously, and returns a zero-length `503` before invoking Next.js when saturated
or draining. It decrements the count exactly once when the response finishes or closes. On SIGTERM
it marks the process draining before closing the listener, rejects races with the same empty `503`,
allows admitted requests to finish for at most 30 seconds, then terminates.
This custom server performs no API proxying or business work. T60 blocks on unit/integration tests
for empty header rejection, the exact 128/129 boundary, finish/close accounting, empty saturation and
draining responses, and bounded shutdown. T64 repeats those cases against the final rootless image.
The public router and FastAPI retain their independent connection, upload, queue, worker, and
storage limits.

## Health, readiness, and failure semantics

FastAPI exclusively owns the public `/health/live` and `/health/ready` contract. Its readiness
continues to check backend profile dependencies and the standalone embedded worker; it never calls
the frontend. The frontend exposes service-internal, non-public `/_frontend/health/live` and
`/_frontend/health/ready` endpoints solely for platform probes:

- frontend liveness proves the Node event loop can answer and performs no dependency call;
- frontend readiness proves the process finished startup and can serve the built route and asset
  manifests from its immutable filesystem;
- neither probe calls FastAPI, storage, a network service, or a browser workflow;
- any missing/corrupt build artifact, incomplete startup, draining process, or event-loop failure
  fails frontend readiness with a content-free `503`.

The frontend Service exposes port 3001 only to the platform probe source; the public router reaches
page port 3000 only. Network policy allows frontend ingress only from that router and the probe
source. The router's ordered denial normalizes the request target and returns content-free `404`
for the reserved prefix, including decoded and case-varied equivalents, before its frontend
catch-all. T64 must prove both internal liveness/readiness success and failure and public denial of
the exact paths, descendants, encoded variants, and case variants.

The router sends browser-page traffic only to frontend-ready replicas and API traffic only to
FastAPI-ready replicas. Monitoring treats browser-route availability and FastAPI service readiness
as separate signals. It must not relabel the frontend probe as Markweave service readiness. The
frontend does not expose application metrics in T58–T64; platform CPU, memory, restart, latency, and
status metrics cover that stateless process, while FastAPI remains the owner of `/metrics`.

## Browser and API trust boundaries

### Sessions, CSRF, and Origin

FastAPI alone issues, validates, rotates, expires, and revokes sessions. The session cookie remains
configurable by name and is always `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, with the existing
absolute-lifetime bound. Neither browser JavaScript nor Next.js application code reads it. The
separate `__Host-md_converter_csrf` cookie remains `Secure`, `SameSite=Lax`, `Path=/`, host-only,
and JavaScript-readable. The typed browser transport copies its decoded value into
`X-CSRF-Token` for every authenticated mutation; missing, malformed, cross-session, or replayed
tokens remain FastAPI failures.

Login is a direct same-origin `POST /api/v1/login`. The browser supplies `Origin`; the router
preserves it; and FastAPI compares it with the exact configured public origin before evaluating
credentials. Non-browser API clients may continue to omit `Origin`. There is no frontend fallback,
credential prevalidation, or alternate login endpoint after cutover. Logout and password renewal
are direct CSRF-protected FastAPI calls. A `401`, password-renewal restriction, revocation, or
expiry is authoritative even when client state says otherwise. Client timers may improve the user
experience but never extend or decide a session.

Server-side Next.js code must not read, forward, persist, log, or transform `Cookie`, `Set-Cookie`,
credentials, CSRF tokens, authorization data, or request bodies. The router makes the cookie rule
structural by removing the complete incoming `Cookie` header and every outgoing frontend
`Set-Cookie` field. Page rendering is public-shell rendering; browser JavaScript obtains the current
principal from `/api/v1/session` and reads only the CSRF cookie for direct FastAPI mutations. No
Server Action or server-side fetch is an authentication bridge.

### CSP and page headers

Every dynamically rendered HTML document response uses a fresh cryptographically random nonce and
this minimum production policy:

```text
default-src 'none';
base-uri 'none';
object-src 'none';
frame-ancestors 'none';
form-action 'self';
script-src 'nonce-<request-nonce>' 'strict-dynamic';
style-src 'self' 'nonce-<request-nonce>';
connect-src 'self';
img-src 'self' data: blob:;
font-src 'self';
manifest-src 'self';
worker-src 'none'
```

The CSP interception hook is `web/proxy.ts` with the named `export function proxy`, as required by
the reviewed Next.js 16 convention. Its matcher covers dynamic document routes and excludes
content-hashed `/_next/static/**` assets and other non-document asset paths. T60's structural and
production-build tests reject deprecated `middleware.ts` or an exported function named
`middleware`.

The same response nonce is passed to framework bootstrap scripts and every emitted inline style
element. T60 must prove on exact Next.js `16.3.4` production dynamic rendering that every HTML
document response has a fresh nonce, cached HTML never reuses one, `script-src` and `style-src`
carry that same nonce, all framework bootstrap scripts and inline style elements carry it, no
inline style attribute or unnonced inline style is emitted, and no generated page or asset requires
eval. T64 repeats that proof against the exact final image bytes. Content-hashed
`/_next/static/**` assets remain byte-stable and immutable-cacheable, with no generated nonce CSP.
Non-HTML and content-free responses also receive no generated nonce CSP; this includes the custom
server's empty header-overflow `431` and saturation/draining `503` responses, which terminate before
Next.js Proxy. Dynamically rendered HTML error documents remain in the nonce checks. No
`unsafe-inline`, `unsafe-eval`, remote script, remote stylesheet, runtime font fetch, analytics,
third-party widget, service worker, or user-controlled CSP source is allowed. If a supported Next.js
patch cannot satisfy this policy, implementation stops for explicit security review instead of
weakening it. Pages also send `Cache-Control: no-store`, `Referrer-Policy: same-origin`, and
`X-Content-Type-Options: nosniff`. The public TLS router owns
and adds to every HTTPS response the exact minimum
`Strict-Transport-Security: max-age=31536000` and
`Permissions-Policy: camera=(), geolocation=(), microphone=(), payment=(), usb=()` headers. HSTS
`includeSubDomains` and `preload` are deliberately absent because Markweave does not control every
sibling subdomain or commit the parent domain to browser preload policy. T64 tests the exact values
on frontend, FastAPI, error, and download responses and rejects duplicate or weaker values.

### Uploads, downloads, and caching

Uploads travel browser -> router -> FastAPI directly. Next.js never receives, buffers, parses,
caches, scans, persists, or forwards a file. The router enforces a transport ceiling no lower than
the configured FastAPI bounded-read ceiling, disables request-body and error-body logging, and does
not turn its limit into the product validation authority. The existing default ClamAV or explicit
non-bypassable trusted-upstream scanner boundary remains mandatory before FastAPI validation or
persistence.

Downloads also travel directly from FastAPI. The frontend uses only same-origin API URLs and honors
server `Content-Disposition`, media type, digest, authorization, `nosniff`, and `Cache-Control:
private, no-store`; it never creates an object URL from an unvalidated error body or invents a
filename. Authenticated API responses and HTML are never stored by Next.js, its data cache, a CDN,
the router, a service worker, or the browser cache. Fetches use `cache: "no-store"` and no Next.js
revalidation tags. Only content-hashed `/_next/static/**` assets may use `public,
max-age=31536000, immutable`; non-hashed assets use bounded revalidation and no user data.

Stable FastAPI error envelopes are displayed as text. Unexpected status, media type, schema, or
non-JSON bodies become a fixed generic English failure. Submitted values, backend response bodies,
stack traces, paths, headers, cookies, and document metadata are never reflected into markup or
frontend logs.

## Image, SBOM, provenance, and publication identity

The backend image remains `ghcr.io/guillaume-lombardo/md-converter`. The frontend is a distinct
public package, `ghcr.io/guillaume-lombardo/md-converter-web`. One Markweave final release binds:

- the PyPI version and source archive;
- the backend registry manifest digest;
- the frontend registry manifest digest;
- one reviewed source SHA and `v<version>` GitHub tag.

Both images are built once from that source SHA, serialized once, scanned, and copied from retained
staged bytes. Each receives CycloneDX and SPDX SBOMs, the complete fixed/unfixed vulnerability
report, a publication receipt relating internal archive identity to public registry digest, and
GitHub artifact provenance for the public digest. The release evidence contains a small manifest
binding both image receipts, version, source SHA, and lockfile digest. Compose, Kubernetes examples,
and quickstarts pin both public manifest digests and reject a version/digest mismatch. Mutable tags
are discovery aliases only and are never a deployment identity.

Publication is one protected release operation. Preflight requires both version tags to be absent
or already equal to the retained bytes. A partial publication does not permit deployment: recover
the missing image and evidence from the retained exact artifact without rebuilding, or declare the
release failed. The same narrow GHCR race documented for the backend applies to the frontend;
package write permission remains isolated to the release workflow. Weekly dependency and image
review, urgent Critical triage, license inventory, secretless OIDC provenance, anonymous pull
verification, and post-publication receipt adoption apply independently to both images.

## Staged migration, cutover, and rollback

1. **T58 architecture:** approve this contract only. Production remains the legacy FastAPI UI.
2. **T59/T60 foundations:** deliver the backend idle policy and an unpublished frontend foundation.
   Generated clients come only from the committed OpenAPI artifact. Test routing cannot replace
   production routes.
3. **T61 authentication parity:** verify login, renewal, logout, protected navigation, and expiry
   while legacy browser routes remain the production default.
4. **T62/T63 workflow parity:** verify conversion and administration behavior independently and
   together. Legacy routes remain deployable and receive all production browser traffic.
5. **T64 parity and rollback gate:** while the candidate branch still contains the legacy renderer,
   run the complete parity/failure matrix and rehearse rollback to the previous released backend
   and route manifest. Resolve every difference before deleting the legacy implementation.
6. **Final source and bytes:** remove the legacy renderer and assets from the candidate source, then
   build and serialize the matched backend and frontend images exactly once. Run strict frontend
   gates, nonce-CSP and ordered-routing tests, FastAPI checks, container smoke, and full final-image
   E2E against those exact staged bytes for standalone and distributed profiles with two users and
   one administrator. Exercise frontend loss, backend loss, internal/public probe separation,
   saturation, draining, restart, cancellation, concurrency, expiry, and authorization. Publish
   only those verified bytes and attach their receipts, SBOMs, and provenance.
7. **Atomic cutover:** drain new submissions as required, take the profile-consistent backup,
   deploy the exact matched published digests, switch the reviewed route table, and require
   frontend readiness, FastAPI readiness, login, one authorized workflow, and operational-route
   checks before admitting general traffic. No post-verification legacy-code removal or image
   rebuild is permitted.

Rollback is release-level, not a mutable feature flag. Preserve the previous matched backend image,
its legacy browser assets, configuration, route manifest, database revision, and both profile backup
identities for the documented rollback window. On a critical cutover failure, stop admission,
restore the previous routing manifest and previous backend digest, and remove the frontend route.
Do not mix frontend and backend releases. If any migration in the release changed persistent schema
or data, follow the upgrade guide: restore the matching pre-cutover database and object backup into
isolated targets before returning traffic to the previous release. If no persistent transition
occurred, the verified previous release may reuse unchanged compatible storage. In both cases,
require readiness and representative authentication, conversion-status, template, and download
checks before completing rollback.

## Legacy behavior preservation inventory

This inventory is an acceptance contract, not a redesign. T61–T64 must turn every item into
unit/component coverage where applicable and preserve it in real-browser/final-image coverage.

### Login, navigation, password renewal, and sessions (T61)

- `/` redirects to `/convert`; unauthenticated protected navigation goes to `/login`; a restricted
  session goes only to `/change-password`; a fully authenticated user going to renewal returns to
  `/convert`.
- The login form has labelled username/password fields, correct `username` and `current-password`
  autocomplete, no public registration, no submitted-value reflection, a non-enumerating invalid
  credential message, exact-Origin validation before password work, and session rotation.
- Successful login routes ordinary users to conversion and renewal-required users to the dedicated
  renewal workflow. The signed-in identity and role-appropriate Convert, Templates, and Users
  navigation remain available and identify the current page.
- Renewal explains that the current password was accepted, uses labelled new/confirmation fields
  with `new-password` autocomplete, rejects blank/mismatched/invalid values safely, permits logout,
  revokes the restricted session on success, and requires fresh login with the new password.
- Restricted sessions can inspect their session, renew, and log out only. Conversion, template,
  user, audit, and policy routes remain denied by FastAPI even if client routing is bypassed.
- Logout, idle expiry, absolute expiry, account state changes, password reset, renewal-requirement
  changes, and authentication-version mismatch are authoritative server revocations. The UI clears
  stale principal state, presents a stable sign-in-again message, and never silently replays a
  mutation after reauthentication.
- Duplicate submissions, late login/session responses, rapid navigation, backend failure,
  malformed responses, missing CSRF state, and repeated `401` responses are fenced and produce one
  deterministic visible result without open redirects.

### Conversion workflow (T62)

- Accept exactly one non-empty `.md` or `.zip` by labelled picker or keyboard-accessible drag and
  drop; show the configured-size, suffix, emptiness, and multiple-file errors before submission,
  while FastAPI remains authoritative for all content and resource validation.
- Default to DOCX and preserve PDF and combined ZIP choices. Preserve Pandoc-default styling,
  resolved preferred/system-fallback selection and label, active-template search by name, template
  description display, immutable current-version selection, and reset to Pandoc default.
- Assign one idempotency key per request-defining file/output/template selection; reuse it after an
  ambiguous transport/server response; invalidate it when request-defining input changes; prevent
  duplicate submit; send multipart data and the CSRF header directly to FastAPI.
- Preserve recent owner jobs (initially ten), reopening by job identifier, the empty-list state,
  step labels, percentage progress, and states queued, running, succeeded, failed, cancelled, and
  expired. Use only safe API failure text.
- Poll immediately then with progressive `1.6x` backoff capped at 10 seconds, honor the current job
  generation, abort superseded search/submission/poll/cancel requests, continue after transient
  status failures, and prevent late responses from overwriting a newer selection.
- Permit cancellation while queued/running, disable duplicate cancellation, continue polling after
  cancellation is requested, and stop only at a terminal state. Do not expose download before
  success or after expiry.
- Download through the owner-authorized API URL and preserve FastAPI's uploaded-stem filename,
  fallback filename, headers, digest, no-store policy, and DOCX/PDF/ZIP bytes. Authorization,
  cancellation, quota `429`, capacity `503`, idempotency conflict, missing job, expired result,
  backend loss, restart recovery, and concurrent execution remain visible and safe.

### Template administration (T63)

- Load every page of the visible template library without truncation; show owner, status, name,
  description, current preference, and an empty state; filter locally by name, description, owner,
  and “My templates” without interpreting user text as markup.
- Any authenticated user can download visible active templates and make or clear a preferred
  template. System fallback resolution remains FastAPI-owned. Hidden controls never substitute for
  authorization.
- Create accepts labelled name, description, comma-separated expected fonts, and exactly one
  non-empty bounded `.docx`; immediate client checks remain advisory and OpenXML, engine, font,
  scanner, storage, ownership, and activation validation remain server-side.
- Replacement preserves the editable comma-separated expected-font field: non-empty input sends
  the trimmed ordered font values, while blank input sends the explicit empty field and clears the
  declaration. T63 tests edit and clear behavior rather than omitting the field.
- Owners and administrators can edit metadata, replace content, load ordered immutable versions,
  download current/history content, and copy-forward restore a non-current version. Every mutation
  uses the current revision ETag in `If-Match`, handles `428`/stale conflicts without overwrite,
  reloads current state, and fences late responses.
- Archive an active template and permanently delete only an eligible archived template after an
  explicit, keyboard-accessible confirmation. Preserve reference/retention guards, administrator
  intervention audit, version identity, duplicate-submit prevention, and clear success/error
  notices.

### User and session-policy administration (T63)

- Only administrators see the Users navigation/section; FastAPI independently denies every
  endpoint to ordinary users. Load and filter accounts by username and show role, active state, and
  password-renewal requirement without hashes, session tokens, or authentication versions.
- Create a user with username, temporary password, and optional next-login renewal. Deactivate or
  reactivate non-administrator accounts, reset any permitted account password with optional
  renewal, and independently require or cancel renewal. Preserve explicit labels, duplicate-submit
  guards, success notices, safe failures, session revocation, audit records, and reload after each
  committed mutation.
- The T59 idle-policy control is administrator-only and shows default/effective duration, approved
  bounds, absolute ceiling, revision, validation failures, stale-write conflicts, and the warning
  that tightening can expire current sessions. FastAPI remains the only clock and policy authority;
  relaxation never revives a session.

### Accessibility, errors, and presentation safety (T61–T64)

- Preserve English page titles, semantic headings/regions, labelled native controls, fieldsets and
  legends, keyboard operation, visible focus, current-page indication, responsive layouts, reduced
  motion, and logical focus restoration after navigation, dialogs, errors, and mutations.
- Errors use assertive live regions; status, results, and progress use polite live regions or native
  progress semantics without announcing every poll noisily. Drag state, loading, disabled,
  cancellation, confirmation, empty, success, and expiry states are perceivable without color
  alone.
- Insert every username, template name/description, filename, API message, and status as text, never
  HTML. Reject malformed API shapes and unexpected content types with fixed generic English text.
  Never expose a stack, path, secret, document content, raw validation input, or arbitrary response
  body.
- Preserve correlation identifiers for diagnostics without accepting caller-controlled IDs or
  adding high-cardinality frontend telemetry. No analytics, remote font, third-party script, or
  browser persistence is introduced.

T64 may remove the legacy FastAPI pages only after the pre-removal parity matrix and rollback
rehearsal succeed. After removal, the complete inventory must pass against the exact final rootless
bytes in both storage-profile deployments before publication and cutover.
