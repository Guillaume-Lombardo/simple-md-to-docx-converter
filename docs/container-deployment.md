# Container deployment

The final UBI 9/Python 3.14 image is built with `scripts/container/build.sh`. The build pins the
base manifest, every downloaded engine archive, the Google signing key and Chrome RPM signature,
the Mermaid npm graph, the `uv` binary, the Python lock, and the complete resolved RPM inventory.
Use `SOURCE_DATE_EPOCH` only when intentionally producing a new reviewed image identity; the
default is fixed for reproducibility.

The build helper keeps the official LibreOffice RPM archive in
`${XDG_CACHE_HOME:-$HOME/.cache}/markweave/toolchain` by default. It verifies the reviewed SHA-256
on every use and downloads from the publisher only when the exact regular cache file is absent.
Set `MARKWEAVE_TOOLCHAIN_CACHE_DIRECTORY` to select another private cache directory. The archive is
passed to Podman as a read-only named build context, so it is not copied into an image layer. CI
uses the same contract for separate checksum-keyed RPM and DEB caches; untrusted runs restore only,
and only a trusted push to `main` may populate them.

The entrypoint performs the hardened runtime preflight and then executes the installed `markweave`
program without translating or restricting its arguments. The default command is `markweave serve`:
it serves HTTP with the embedded worker for the one-replica standalone profile and without an
embedded worker for the distributed profile. Distributed worker containers use `markweave worker`.
Command overrides can invoke `doctor`, `migrate`, `backup`, `restore`, or any supported HTTP client
command through the same entrypoint. Container-only `api`, `embedded-worker`, and `external-worker`
commands are not part of this image contract.

Every mode refuses UID 0, non-empty effective or bounding capabilities, absent
`no-new-privileges`, a writable root, or missing dedicated `/tmp`, `/work`, and `/dev/shm` mounts.
Run as an arbitrary platform-assigned UID in group 0. Never grant a capability, privileged mode,
host networking, an unconfined seccomp profile, or Chrome's `--no-sandbox` flag. `/tmp` and
`/dev/shm` are bounded memory volumes. `/work` is a bounded disk-backed ephemeral volume in the
deployment examples. `/data` is mounted only for standalone persistence.

## Profiles and resource policy

`deploy/standalone.yaml.example` runs one `serve` replica with a ReadWriteOnce `/data`
claim. `deploy/distributed.yaml.example` separates `serve` API and `worker` deployments and expects
PostgreSQL plus an AWS S3-compatible store. RustFS is the test implementation in
`deploy/rustfs-ci.yaml`; there is no RustFS-specific application API.

The repository's public Compose quickstart pins the matched `0.6.1` backend and Next.js frontend
images by their verified registry digests. The backend starts its standalone role with
`markweave serve`; the same-origin router exposes the browser and API routes. The deployment
examples use `markweave serve` for API roles and `markweave worker` for distributed workers.

The examples are workload fragments, not complete production stacks. They deliberately contain
`${...}` placeholders. Render them only after supplying every
approved value. `WORKER_MEMORY_BUDGET_BYTES` and `WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES` appear both
in the application configuration and the worker resource limits so the approved values are enforced
without unit conversion. Do not introduce example values as production defaults. The Localhost
Chrome seccomp profile is `spikes/toolchain/chrome-seccomp.json`; install that exact reviewed file
on each worker node before applying a manifest. OpenShift validation remains deferred, so these
manifests do not claim OpenShift compatibility.

Local ClamAV is the default upload-scanning boundary. Its external `clamd` service is selected
through `MARKWEAVE_CLAMAV_HOST`, port, and timeout, and its unavailability remains fail-closed.
Set `MARKWEAVE_MALWARE_SCANNING_MODE=trusted-upstream` only when an upstream proxy scans every
conversion and template upload before forwarding and default-deny network policy makes direct or
alternate application access impossible. This mode makes no ClamAV connection and logs a startup
warning; it is not a general-purpose antivirus-disable switch.

The operator must supply Services, ingress or routes, default-deny NetworkPolicies with explicit
allowances, ConfigMaps, Secrets, and either ClamAV or the complete trusted-upstream boundary. A
distributed deployment also needs PostgreSQL and an S3-compatible store. Permit inbound API and
metrics-scraper traffic and only the required DNS, selected scanner boundary, PostgreSQL, and S3
egress. Input validation still forbids document-controlled remote access; do not grant general
Internet egress to API or worker pods. The application keeps no durable quarantine.

Credentials and the initial administrator password belong in a Secret, never a ConfigMap, manifest,
command line, or image. Inject them by the platform's secret mechanism and apply least-privilege
database and bucket credentials. Rotate the bootstrap administrator password after first sign-in.
The image also requires explicit conversion limits for archive compression, image dimensions and
SVG structure, Mermaid source/output dimensions, PDF structure, and cancellation polling. Configure
the locked `mmdc`, Chromium, Pandoc, LibreOffice, and font-manifest paths; none of these limits is an
implicit production recommendation.

The complete environment inventory and cross-field constraints are in
[configuration.md](configuration.md).

## TLS and public origin

Terminate TLS before authenticated browser traffic reaches the application. The runtime starts
Uvicorn with proxy-header trust disabled, so `Forwarded` and `X-Forwarded-*` headers do not define
the security origin. For an HTTPS-terminating proxy, set `MARKWEAVE_PUBLIC_ORIGIN` to the exact
browser-visible origin, for example `https://converter.example.invalid`; an explicit non-default
port is permitted, but paths, queries, fragments, and user information are rejected. Origin checks
then compare against that configured value. When the setting is absent, they use the direct ASGI
request scheme, host, and port, which is suitable for direct or end-to-end TLS where those values
already match the browser origin.

Do not weaken Origin, CSRF, or Secure-cookie controls and do not enable broad proxy-header trust as
a substitute. Restrict accepted hostnames at the ingress, forward only to the service, and keep the
API unreachable over unintended plaintext paths.

The repository's `quickstart-simple.sh up --insecure` mode is an explicit exception for temporary
loopback-bound SSH-tunnel evaluation. It disables both local malware scanning and login-origin
validation and is therefore prohibited behind an ingress, reverse proxy, or any production or
network-accessible endpoint.

## Immutable rollout

Deploy an approved registry digest such as `registry.example/image@sha256:...`, never a mutable tag.
Record the digest with the rendered configuration and backup/recovery evidence. Install the exact
reviewed Localhost Chrome seccomp profile on every eligible worker node before scheduling a worker;
the API-only distributed deployment uses RuntimeDefault. Resolve every resource placeholder and
make `/work` a bounded disk-backed ephemeral volume while `/tmp` and `/dev/shm` are bounded memory
volumes.

Use the drain procedure in [operations.md](operations.md) before disruptive standalone changes and
before a distributed change requiring storage quiescence. Roll back by digest and compatible
configuration; never rebuild an old tag and call it the same release. Back up and restore according
to [recovery.md](recovery.md).

## Verification and supply chain

Run:

```bash
bash scripts/container/build.sh localhost/md-converter:t20
bash scripts/container/smoke.sh localhost/md-converter:t20
bash scripts/container/api-smoke.sh localhost/md-converter:t20
bash scripts/container/distributed-api-smoke.sh localhost/md-converter:t20
bash scripts/container/supply-chain.sh localhost/md-converter:t20 artifacts/container
```

The smoke harness uses real rootless Podman, an arbitrary UID supported by the host subordinate-ID
map, the reviewed Chrome seccomp profile, read-only root, no capabilities, `no-new-privileges`, and
bounded cgroups and writable areas. It executes Pandoc, sandboxed Mermaid/Chrome, and LibreOffice.
The standalone smoke starts the embedded worker on the image's SQLite 3.34 runtime and performs
real authenticated template publication plus DOCX, PDF, and combined asynchronous conversions. It
polls jobs, inspects OpenXML/PDF/combined bytes and canonical traceability sidecars, and verifies
source mismatch and forbidden-suffix failures. The distributed smoke repeats that workflow through
PostgreSQL, RustFS, a worker-free API, and an external worker; it also verifies the worker metrics
listener and a clean SIGTERM exit. A protocol-faithful clean-only INSTREAM sidecar permits positive
uploads; dedicated tests retain clean, malware, unavailable, and malformed ClamAV coverage. The
final-image E2E suite adds three-user, two-profile recovery and concurrency coverage.

The supply-chain command downloads SHA-locked Syft and Grype releases, writes CycloneDX and SPDX
JSON SBOMs, retains the complete fixed and unfixed Grype report, and separately fails on Critical
findings with an available fix. Unfixed Critical findings remain explicit and require mitigation or
rollback evidence before release; they are never silently filtered. CI retains both SBOMs, the
report, and bounded image metadata/digests for 30 days as verification evidence. Scanner databases
remain time-varying evidence. Release publication, provenance, and registry attachment are covered
by [releasing.md](releasing.md).

## Production conversion runtime

The processor loads the frozen owner-scoped source and, for versioned mode, the exact verified
template version. Pandoc-default mode resolves no template. It verifies the persisted filename,
kind, size, and digest without inferring source type from content, then delegates validation,
Mermaid rendering, DOCX creation, and PDF creation to the existing bounded adapters. PDF
traceability is published as a canonical sidecar; `both` output is also a
deterministic ZIP containing `document.docx`, `document.pdf`, and `traceability.json`. Embedded
worker failure makes standalone readiness fail. External workers expose process-local metrics and
stop on SIGINT or SIGTERM.

The UBI runtime provides SQLite 3.34.1. SQLite mutations acquire `BEGIN IMMEDIATE`, execute their
conditional update, and select the result in the same transaction; PostgreSQL keeps
`UPDATE ... RETURNING` and `SKIP LOCKED`. Tests install a SQLite 3.34 grammar guard so any emitted
`UPDATE ... RETURNING` fails even on newer developer SQLite libraries.

Build-only Corepack/pnpm tooling and their store, plus unused curl, OpenSSL, and Apache HTTPD
command-line executables, are
absent from the runtime filesystem. Required shared libraries and the complete RPM inventory remain
recorded. This reduces unused attack surface without altering the vulnerability threshold.

## Approved Next.js cutover topology

The repository's `0.6.1` continuation source no longer contains FastAPI browser pages. The public
default remains on the last deployable release until both `0.6.1` image receipts are verified and a
separate adoption pull request pins their digests. T64 implements the separate frontend image and the
literal one-origin routing, resource, probe, supply-chain, and rollback contract defined in
[the reviewed Next.js migration architecture](nextjs-migration-architecture.md).

The approved target uses a UBI 9 Node.js 24 builder and UBI 9 Node.js 24 minimal runtime pinned by
the reviewed Linux/AMD64 digests. The process is stateless, arbitrary-UID, read-only-root, and
capability-free, with only bounded memory-backed `/tmp`; it mounts neither `/data` nor `/work` and
receives no backend service credentials. The standalone profile adds one frontend replica, while
the distributed profile may scale frontend replicas independently. Both keep browser pages and
FastAPI behind one TLS origin.

The public `/health/live` and `/health/ready` paths continue to reach FastAPI. Platform probes reach
the frontend's non-public `/_frontend/health/live` and `/_frontend/health/ready` paths on the
Service-only probe port 3001; the public router reaches page port 3000 only. The public router must
normalize the request target and order a content-free `404` denial for `/_frontend/health`, every
descendant, and decoded or case-varied equivalents before its frontend catch-all. Network policy
permits frontend ingress only from the public router and platform probe source. Never expose the
probe port publicly or treat frontend readiness as backend/storage readiness. T64 verifies internal
live/ready success and failure plus public denial of exact, descendant, encoded, and case-varied
paths. Deployment manifests must apply the exact initial frontend budgets from the migration
architecture and keep backend, worker, scanner, storage, and document budgets independent.

The public router strips the complete `Cookie` header before forwarding any method on any
frontend-owned route to port 3000, including named pages, `/_next/**` assets, and unknown catch-all
paths. It strips every `Set-Cookie` field from every frontend response regardless of method, status,
or content type. It preserves both directions unchanged when routing exact `/api/v1`, `/api/v1/**`,
and public operational routes directly to FastAPI. T60's routing fixture and T64's
production-router final-image tests must cover named pages, assets, unknown paths, non-GET requests,
multiple response fields, exact API-base and descendant preservation, and a representative
operational route.

The frontend uses the T60-owned `web/server.mjs` supported custom-server entry point, not Next.js
standalone output. Node rejects headers beyond 16 KiB; the server admits at most 128 simultaneous
requests, emits a zero-length `431` for header overflow and zero-length `503` responses for
saturation and drain races, and bounds SIGTERM draining to 30 seconds. The runtime therefore
contains the `.next` production build and exact
pruned production dependency graph. T60 proves the server contract in isolation and T64 repeats it
against the final rootless image.

The TLS router owns the response-wide minimum headers
`Strict-Transport-Security: max-age=31536000` and
`Permissions-Policy: camera=(), geolocation=(), microphone=(), payment=(), usb=()`. Do not add HSTS
`includeSubDomains` or `preload` without a separate domain-ownership decision. T64 verifies exact,
non-duplicated values across frontend, FastAPI, error, and download responses.
It also applies the positive `ROUTER_UPSTREAM_TIMEOUT_MS` inactivity bound to backend and frontend
relays and destroys upstream work when the downstream connection closes. The loopback Compose
candidate uses 30 seconds; production manifests require the operator to supply the reviewed value.

At release, deploy only a matched backend/frontend version pair pinned by both verified registry
manifest digests. A partial pair, mutable tag, mixed version, or frontend whose CSP/routing probes
fail is not deployable. Preserve the prior backend digest containing the legacy UI and its route
manifest until the cutover rollback window and rehearsal are complete.
