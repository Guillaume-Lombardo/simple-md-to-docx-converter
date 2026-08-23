# Orchestration State

Last verified: 2026-08-23

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Verified State

- `main` is clean at `fd44c1e0933219ea99c891e53bf7b4ee982e6b6a` and matches `origin/main`.
  Main CI run `32658422667` passed.
- PR #20 was squash-merged as `33f86a05`. Its approved T00/T12 decisions are present in the
  specification, ticket mirrors, and Linear issues G1L-310/G1L-324.
- T01, T02, T03, T05, T06, and T12 are delivered. T00 remains `In Progress`; T04 remains dependent
  on completion of T00 evidence.
- PR #24 delivered both persistent storage profiles as squash `fd44c1e`. Main CI ran the real
  standalone, PostgreSQL/RustFS distributed, functional, and CI-infrastructure domains successfully.
- PR #22 delivered the independently reviewed Docker/rootless-Podman harness and durable T00
  evidence as squash `85b43b62`; Linear G1L-310 remains synchronized as `In Progress`.
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
  authorization contracts, stable errors, and durable SQLite/PostgreSQL authentication persistence.
- Coherent standalone SQLite/atomic-file and distributed PostgreSQL/AWS-S3-compatible storage
  profiles, with Alembic migrations, shared contracts, real RustFS integration, cheap readiness,
  and documented backup/restore mechanics.
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

1. Continue T00 with the minimum Chrome seccomp/user-namespace composition and k3s proof; keep
   OpenShift proof deferred and do not weaken the browser sandbox.
2. Select T14 as the next ready product ticket; T06 and T12 are verified `Done`, while T13 still
   waits for T11 and T04 still waits for T00.
3. Preserve the explicit T12 final-image rootless E2E debt in T20/T21.
4. Reconstruct repository, Linear, CI, and worker truth after each transition and rewrite this file
   as current state rather than a chronology.

## Validation

- PR #20 checks passed in run `32655376550`; the exact merged SHA passed main run `32655450957`,
  including the protected `CI / gate`.
- PR #22 checks passed on exact head `2b920706`; squash `85b43b62` passed main run `32656611491`,
  including the protected `CI / gate`.
- PR #24 checks passed on exact head `add244d4`; squash `fd44c1e` passed main run `32658422667`,
  including the protected gate and real standalone/distributed storage domains.
- Linear G1L-310 and G1L-324 were fetched by identifier and match the approved repository scope;
  G1L-310 is `In Progress` and G1L-324 is ready for synchronized closure as `Done`.
- T00 Docker and rootless Podman harnesses pass tmpfs, disk-backed, security, and failure probes;
  their canonical suites pass 122 tests at 98.73% application coverage. Chrome remains a deliberate
  safe failure pending the minimum sandbox profile.
- Final product validation has not run; the product is incomplete.
