# Orchestration State

Last verified: 2026-08-23

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Verified State

- `main` is clean at `33f86a05c88395d7a6535412390e4624231ba5ff` and matches `origin/main`.
  Main CI run `32655450957` passed.
- PR #20 was squash-merged as `33f86a05`. Its approved T00/T12 decisions are present in the
  specification, ticket mirrors, and Linear issues G1L-310/G1L-324.
- T01, T02, T03, T05, and T06 are delivered. T00 and T12 are `In Progress` in Linear; T04 remains
  dependent on completion of T00 evidence.
- T12/G1L-324 is delegated to an isolated implementation worker and has started. Its ticket mirror
  is being synchronized on the implementation branch; no implementation result is claimed yet.
- T00 rootless-runtime work is delegated independently. Podman 5.4.2 is installed and runs
  rootless with seccomp, cgroups, and user namespaces available. The committed harness on `main`
  still documents Docker-only evidence; the T00 worker is validating a runtime selector and Podman
  execution before review and integration.
- Chrome remains safely blocked under the strict profile until the minimum seccomp/user-namespace
  composition is proven. The sandbox stays enabled, `--no-sandbox` is forbidden, k3s follows the
  Podman proof, and OpenShift proof remains deferred.

## Approved Product Decisions

- Use official publisher artifacts, verify signatures or attestations when available, lock every
  accepted digest/checksum, accept Pandoc SHA-256 where no detached signature exists, review CVEs
  weekly, and handle Critical findings urgently.
- Use `commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html` and reject raw
  HTML before Pandoc.
- Package Liberation plus Carlito/Caladea, use DejaVu as fallback, and add Noto only for explicitly
  required scripts.
- Keep the distributed object-store adapter provider-neutral and AWS S3-compatible. Use RustFS,
  never MinIO, for CI and k3s.
- T12 must include shared storage contracts and real PostgreSQL/RustFS integration success and
  failure coverage. Only final-image rootless E2E is deferred to T20/T21, with explicit PR
  justification and independent reviewer approval.

## Delivered Foundation

- Python 3.14/`uv` packaging, repository conventions, durable orchestration memory, protected
  `main`, selective GitHub Actions with the strict `CI / gate`, Ruff/ty/Pytest quality gates, and
  90% coverage enforcement.
- FastAPI configuration and health endpoints, local account administration, revocable sessions,
  authorization contracts, stable errors, and temporary in-memory persistence ports ready for T12
  replacement.
- Reproducible T00 compatibility evidence for Pandoc, Mermaid/Chrome, Fontconfig, LibreOffice,
  arbitrary UID, read-only root, no network, dropped capabilities, writable areas, and cgroup
  envelopes, with the remaining sandbox/runtime gaps stated explicitly.

## Blockers and Risks

- Chrome/Mermaid cannot be declared supported until the minimal sandbox profile passes Podman and
  k3s validation. OpenShift compatibility cannot be claimed until the deferred target proof runs.
- Exact font artifacts/substitution order and explicit Noto scripts remain T10 work.
- Resource limits, RPO/RTO, retention, quotas, antivirus, cleanup, GitHub Actions heavy-job budget,
  PDF/A, and table-of-contents support remain deliberately unresolved or configurable.
- Git SSH transport is not usable on this VM because the configured identity resolves to the public
  `~/.ssh/codex-dev.pub` file. Read-only `gh` API access works; future Git transport must use an
  approved authenticated path without exposing credentials or silently changing SSH configuration.
- GitHub merge queue is unavailable for this user-owned public repository, so merges remain
  serialized by the orchestrator.

## Next Actions

1. Complete T00 Podman/runtime-selector validation, commission independent review, then integrate
   only the verified rootless evidence.
2. Continue T12 implementation in parallel, including both storage profiles, Alembic, atomic files,
   shared contract tests, and real PostgreSQL/RustFS integration coverage.
3. Commission an independent T12 review before integration and preserve the explicit T20/T21
   final-image E2E debt.
4. Reconstruct repository, Linear, CI, and worker truth after each transition and rewrite this file
   as current state rather than a chronology.

## Validation

- PR #20 checks passed in run `32655376550`; the exact merged SHA passed main run `32655450957`,
  including the protected `CI / gate`.
- Linear G1L-310 and G1L-324 were fetched by identifier and match the approved repository scope;
  both report `In Progress`.
- Local Podman reports version 5.4.2, rootless mode, seccomp enabled, and the systemd cgroup manager.
  The worker's full harness result and Chrome sandbox proof are still pending review.
- Final product validation has not run; the product is incomplete.
