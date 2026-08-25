---
ticket: T23
linear_id: G1L-333
linear_url: https://linear.app/g1lom/issue/G1L-333/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T23 - Complete user, developer, and operations documentation

## Objective

Complete English user, template, administrator, API, operations, storage, queue, agent, recovery, and deployment documentation.

## Acceptance criteria

- The implementation satisfies the T23 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.
- The README leads a casual user from prerequisites through a healthy local service and first
  conversion before presenting operations, architecture, and development details.
- A tested root `compose.yaml` runs the released standalone image with real ClamAV, persistent data,
  loopback-only HTTP, immutable image identities, and the final rootless security contract.
- The documentation index exposes role-based user, API, configuration, operations, recovery,
  deployment, architecture, development, and agent guides; every runtime setting and local link is
  checked automatically.
- TLS-terminating deployments can configure one exact public HTTP(S) origin without trusting
  forwarded headers, while direct HTTP behavior remains backward compatible when it is unset.

## Dependencies

- T22

## Progress

- 2026-08-25: Started after T22 was verified `Done`. The documentation will lead with a
  casual-user quick start backed by a tested `compose.yaml`, then provide progressively deeper
  user, operations, architecture, development, recovery, and agent-workflow guidance.
- 2026-08-25: Implemented the role-based documentation set, complete runtime configuration
  reference, current architecture and operations guidance, safe release/recovery procedure, and a
  casual-user README. Added `MD_CONVERTER_PUBLIC_ORIGIN` with unit and real-HTTP integration tests
  so the documented TLS reverse-proxy design keeps exact Origin validation without trusting
  forwarded headers.
- 2026-08-25: The Compose evaluation profile was exercised with the published `0.3.0` image and
  official ClamAV 1.4 LTS image at pinned digests. Both services became healthy,
  `/health/ready` returned HTTP 200, only `127.0.0.1:8080` was published, ClamAV had no host port,
  the application had no Internet egress, and persistent data/signature volumes survived the
  non-destructive stop. Six documentation/Compose contract tests and 138 targeted tests pass;
  Ruff formatting and linting, `ty`, `docker compose config`, and `git diff --check` pass.
- 2026-08-25: The canonical suite excluding Pandoc, Mermaid, and LibreOffice passes with 1,514
  tests and 95.54% coverage when the documented PostgreSQL/S3 test services are supplied. The full
  host suite reaches 1,521 passing tests and 95.49% coverage but cannot run 37 engine tests because
  Pandoc, Mermaid/Chromium, LibreOffice, and the locked fonts are not installed on the host; the
  pull-request matrix must verify those final-image engine boundaries before completion.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
