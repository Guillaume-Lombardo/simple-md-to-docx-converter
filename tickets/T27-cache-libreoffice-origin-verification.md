---
ticket: T27
linear_id: G1L-388
linear_url: https://linear.app/g1lom/issue/G1L-388/
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T27 - Cache LibreOffice artifacts and verify quickstart login origin

## Objective

Prevent transient LibreOffice CDN failures from repeatedly breaking CI container and
document-engine jobs, and make custom-port quickstarts verify that the exact browser-visible login
origin reaches the running application across supported Compose providers.

## Acceptance criteria

- CI restores the exact LibreOffice DEB and RPM archives from checksum-keyed caches before using
  the publisher CDN.
- Every restored or downloaded archive is verified against its reviewed SHA-256 before
  installation or image construction.
- Cache writes are limited to trusted pushes on `main`; pull requests and other contexts are
  restore-only.
- Container builds consume the verified RPM archive without embedding the archive in a final image
  layer and retain an official-publisher download fallback on a cache miss.
- The simple and secure quickstarts pass the selected port and exact public origin through
  supported Compose providers deterministically.
- The quickstarts verify the running application's effective public origin before reporting
  readiness.
- Real quickstart coverage sends a browser-equivalent login `Origin` on a non-default port and
  verifies that it is accepted while a hostile origin remains rejected.
- Relevant canonical checks pass, with unavailable external engines or services reported
  explicitly.

## Dependencies

- T22
- T26

## Progress

- 2026-08-28: Started after repeated LibreOffice CDN resets failed the release and ready-PR
  container matrices, and after a reported Podman quickstart login rejection on port 11279. A local
  reproduction with the published `0.3.4` image accepted the configured origin and rejected a
  hostile origin, so provider-level origin propagation and an explicit runtime check are included
  in the scope.
- 2026-08-28: Added checksum-keyed, restore-only-on-PR LibreOffice DEB and RPM caches with trusted
  `main` writes, SHA-256 verification on downloads and cache hits, and a read-only named build
  context for the container RPM. Both quickstarts now pass the selected origin explicitly through
  Compose; the simple helper also proves the effective environment and browser-equivalent login
  behavior before reporting readiness. The non-default-port E2E contract rejects a hostile origin.
- 2026-08-28: Ruff, `ty`, Bash syntax, ShellCheck, 23 Web tests, 1,349 unit tests at 93.64% coverage,
  and 225 focused CI/container/quickstart tests pass. A real checksum-verified archive download,
  rootless image build, image smoke, standalone API smoke, and port-11280 Podman quickstart pass.
  The canonical suites reached 1,545 and 1,552 passing tests respectively but remain blocked by
  the machine's unavailable PostgreSQL/RustFS configuration and external-engine environment. The
  distributed container smoke reproduces `mermaid_unavailable` unchanged with the published
  `0.3.4` image, confirming a local baseline limitation rather than this change.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
or progress changes.
