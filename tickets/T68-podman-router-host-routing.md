---
ticket: T68
linear_id: G1L-536
linear_url: https://linear.app/g1lom/issue/G1L-536/t68-restore-podman-insecure-quickstart-host-routing
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T68 - Restore Podman insecure quickstart host routing

## Objective

Restore host access to the published Next.js router in the rootless Podman trusted-upstream and insecure simple quickstarts while preserving the loopback-only host boundary and the CNI-free shared-network-namespace topology.

## Acceptance criteria

* Bind the router to the shared slirp4netns namespace interface that receives Podman's host-port forwarding; do not limit the process to the namespace's internal loopback.
* Keep the host publication strictly on `127.0.0.1`, retain the canonical public-host allowlist, and preserve the private backend/frontend origins and CNI-free shared namespace.
* Add rendered-Compose regression coverage that distinguishes the namespace bind address from the loopback-only host publication and internal readiness probe.
* Run the real rootless Podman insecure quickstart against the exact published `0.6.1` backend/frontend digest pair and prove the browser page and direct API routing from the host.
* Preserve bounded failure cleanup and verify `down` removes only the disposable work volume while retaining documented application data.
* Run applicable formatting, linting, type, Compose, container, integration, and hosted final-image E2E checks.

## Dependencies

* T64

## Implementation boundary

* Own only the rootless Podman shared-namespace router binding, its quickstart/Compose tests, required CI selection, and directly affected operational documentation.
* Do not change router authorization, backend/frontend business behavior, published image identities, or non-Podman topology.

## Quality requirements

* Preserve the loopback-only host publication and rootless Podman security boundary.
* Add automated regression coverage for the rendered configuration and real host-to-container path.
* Keep repository artifacts and user-facing text in English.

## Progress

* 2026-09-03: Started after a production-user reproduction on rootless Podman. The router returned `200` on `127.0.0.1:3100` inside the shared namespace while the host's loopback-only forwarded port reset the connection. A local reproduction against the exact published `0.6.1` image pair confirmed that slirp4netns forwards to the namespace address rather than its internal loopback.
* 2026-09-03: Changed only the shared-namespace router bind to `0.0.0.0`; the host publication remains `127.0.0.1:${MARKWEAVE_PORT}:3100`, and the internal readiness probe remains on `127.0.0.1:3100`. Rendered-Compose and static regression tests now assert all three distinct boundaries.
* 2026-09-03: Added a hosted Compose-domain E2E for `quickstart-simple.sh up --insecure`. A real local rootless Podman run passed against the exact published `0.6.1` backend/frontend digests, proving the Next.js login page and direct FastAPI session route through the host port, canonical-host rejection, helper shutdown, bounded `down`, disposable-work removal, and retained data/password/template state.
* 2026-09-03: Local validation passed Ruff formatting/lint, `ty`, root browser tests (16), targeted Compose/CI tests (127), shell syntax/ShellCheck, and the real rootless Podman insecure E2E. The canonical non-engine Pytest selection passed 2,334 tests and coverage (95.66%) but ended non-zero only because the local PostgreSQL and RustFS test endpoints were not configured (30 setup errors and 3 S3 failures); the ready pull request must run those services and the full hosted domain matrix.
* 2026-09-03: Independent review found that Podman versions may canonicalize container `ImageName` by dropping the tag. The E2E now separately proves the rendered tag-plus-digest references and compares each running container's inspected image digest. The 127 targeted tests and the complete real rootless Podman insecure E2E passed again after this correction.
* 2026-09-03: A second review found that `podman port` reports the shared namespace owner's mapping even when queried with the router container. The E2E now asserts the router's own `HostConfig.PortBindings` is null, retains the rendered-Compose no-port assertion, and proves the effective loopback mapping on the namespace owner. The 127 targeted tests and complete real E2E passed again through final cleanup.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
