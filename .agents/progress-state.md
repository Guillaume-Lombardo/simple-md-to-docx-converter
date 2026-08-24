# Orchestration State

Last verified: 2026-08-24

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Verified State

- `main` is clean and matches `origin/main`; delivered history through T00 k3s PR #30 is verified
  below.
- T00, T01, T02, T03, T05, T06, T12, and T14 are delivered. Linear G1L-310 is synchronized as
  `Done`, and T04 implementation is active on `feat/T04-golden-infrastructure`.
- T04 implementation now includes the complete manifest-owned corpus, provenance-pinned generated
  DOCX and adversarial ZIP fixtures, pre-read bounded archive/OpenXML inspection, bounded one-pass
  RGBA raster comparisons, reusable fixtures, exhaustive unit/integration coverage, an active T04
  CI domain, and documentation. It remains `In Progress` pending independent review and
  verification on `main`.
- PR #26 delivered the independently reviewed rootless Podman Chrome sandbox proof. The sandbox
  stays enabled, `--no-sandbox` is forbidden, and OpenShift proof remains deferred.
- PR #27 delivered the independently approved T14 ownership, visibility, search, preference, and
  fallback foundations as squash `c296d458b2d64c3ee1d9cfbb6f65e8f86ff440b9`. The local ticket
  and Linear G1L-325 are fully synchronized as `Done`.
- PR #28 delivered the approved T00 scope and synchronized orchestration and ticket state as squash
  `b2e5b3965ad05448c56d6fd857191489f8a94173`; its main validation passed the protected gate.
- The PM-authorized local k3s T00 validation passed with the checksum-locked Chrome seccomp profile
  and fail-closed controls. The exact namespace, Localhost profile, and imported image were removed
  and verified absent. The cluster is no longer needed, and the orchestrator stopped k3s.
- PR #30 delivered the independently approved T00 k3s proof: cluster-global names are unique and
  collision-checked,
  installed profile integrity is verified, cleanup is ownership- and UID-preconditioned, and offline
  negative probes cover collision, tampering, acquisition failure, and proxy lifecycle. The proxy
  is terminated as a complete process group. Exact PID/start-time baselining catches survivors even
  after k3s rewrites argv; baseline identities are excluded from token signaling, and an existing
  token collision blocks launch. Namespace deletion requires this run's valid create receipt and
  captured UID rather than public metadata alone. Hardened live run `reviewfix06` removed every
  exact run resource; the added failure paths are verified offline. The exact squash and its
  protected main gate passed.

## Approved Product Decisions

- Use official publisher artifacts, verify signatures or attestations when available, lock every
  accepted digest/checksum, accept Pandoc SHA-256 where no detached signature exists, review CVEs
  weekly, and handle Critical findings urgently.
- Use `commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html` and reject raw
  HTML before Pandoc.
- Package Liberation plus Carlito/Caladea, use DejaVu as fallback, and add Noto only for explicitly
  required scripts. T10 owns exact artifacts, licenses, substitutions, and script coverage.
- Keep the distributed object-store adapter provider-neutral and AWS S3-compatible. Use RustFS,
  never MinIO, for CI and k3s.
- T12 must include shared storage contracts and real PostgreSQL/RustFS integration success and
  failure coverage. Only final-image rootless E2E is deferred to T20/T21, with explicit PR
  justification and independent reviewer approval.
- Keep production limits, RPO/RTO, retention, quotas, antivirus integration, and cleanup
  configurable until T18.
- PDF/A output and automatic Word/PDF table-of-contents generation are outside the initial scope.

## Delivered Foundation

- Python 3.14/`uv` packaging, repository conventions, durable orchestration memory, protected
  `main`, selective GitHub Actions with the strict `CI / gate`, Ruff/ty/Pytest quality gates, and
  90% coverage enforcement.
- FastAPI configuration and health endpoints, local account administration, revocable sessions,
  authorization contracts, stable errors, and durable SQLite/PostgreSQL authentication persistence.
- Coherent standalone SQLite/atomic-file and distributed PostgreSQL/AWS-S3-compatible storage
  profiles, with Alembic migrations, shared contracts, real RustFS integration, cheap readiness,
  and documented backup/restore mechanics.
- Immutable template ownership derived from the authenticated actor, global active-template
  visibility, deterministic search, owner/administrator authorization, preferred templates, and a
  system fallback, with shared real SQLite/PostgreSQL service contracts.
- Reproducible T00 compatibility evidence for Pandoc, Mermaid/Chrome, Fontconfig, LibreOffice,
  arbitrary UID, read-only root, no network, dropped capabilities, writable areas, and cgroup
  envelopes on Docker, rootless Podman, and local k3s, with deferred OpenShift compatibility stated
  explicitly.

## Blockers and Risks

- Chrome/Mermaid is proven on rootless Podman and local k3s. OpenShift compatibility cannot be
  claimed until the deferred target proof runs.
- Exact font artifacts/substitution order and explicit Noto scripts remain T10 work.
- Production limits, RPO/RTO, retention, quotas, antivirus, and cleanup remain configurable T18
  work. GitHub Actions heavy-job timeouts, full-suite frequency, and usage budget remain T22 work.
- Git SSH transport is not usable on this VM because the configured identity resolves to the public
  `~/.ssh/codex-dev.pub` file. Read-only `gh` API access works; future Git transport must use an
  approved authenticated path without exposing credentials or silently changing SSH configuration.
- GitHub merge queue is unavailable for this user-owned public repository, so merges remain
  serialized by the orchestrator.

## Next Actions

1. Implement T04's reference corpus, reusable fixtures, deterministic DOCX/OpenXML and PDF-raster
   comparison infrastructure, marker coverage, and tests without absorbing downstream conversion
   behavior from T07-T11; implementation is ready for independent review.
2. Obtain independent review, verify T04 on `main`, synchronize Linear G1L-314, and unblock T07/T08.
   T13 still waits for T11.
3. Preserve the explicit T12 final-image rootless E2E debt in T20/T21.
4. Reconstruct repository, Linear, CI, and worker truth after each transition and rewrite this file
   as current state rather than a chronology.

## Validation

- PR #26 head `329fd96631b13f2cb13180fcad7176e9697e24f7` passed run `32668812723`;
  squash `4758cbf7682ea815e797e78b871384247a72f884` passed main run `32668864601`,
  including the protected `CI / gate`.
- PR #27 exact rebased head `22fcf501ca5e0079e27cd46711fc499cf92ea7e3` passed run
  `32669541287`, received independent approval with no findings and 100% changed-line coverage
  (299/299), and was squash-merged as `c296d458b2d64c3ee1d9cfbb6f65e8f86ff440b9`. Exact-main run
  `32669621800` passed functional, standalone-storage, distributed-storage, and protected-gate
  jobs. T14 final-image E2E is genuinely not applicable; the existing Starlette warning is
  non-blocking.
- PR #28 head `3f102ee668c95e719e8c22baf20ca944334a4994` was squash-merged as
  `b2e5b3965ad05448c56d6fd857191489f8a94173`; that exact squash passed main run `32669940608`,
  including the protected `CI / gate`.
- PR #30 exact head `7b673ae18ea0e9ce3f6c02eda0ba1e2af1e89fc3` received independent
  security approval with no findings, passed run `32675125046`, and was squash-merged as
  `b927d060d60b6eacdabb872e627768defcd58126`. That exact squash passed main run `32675177329`,
  including the protected `CI / gate`.
- Linear G1L-325 was fetched by identifier and reports `Done`, with the PR #27 attachment and exact
  delivery entry matching the local `Done` ticket.
- Linear G1L-310 reports `Done`, contains the PR #30 completion evidence and attachment, and matches
  the local T00 mirror. Linear G1L-314 reports `In Progress`; its T00 and T01 dependencies are both
  verified `Done`.
- T04 formatting, Ruff, and ty checks pass. Its focused selection passes 120 tests; its helper-only
  coverage run passes 89 tests at 99% branch coverage; its active CI integration command passes 57
  tests; and the service-independent suite passes 270 tests at 98% application coverage. Both canonical Pytest
  commands reach the expected 10 PostgreSQL/RustFS failures because this worktree has no test-service
  environment variables; all other 270 tests pass. T04 has no user-visible or operational workflow,
  so final-image E2E coverage is not applicable.
- T00 Docker and rootless Podman harnesses pass tmpfs, disk-backed, security, and failure probes;
  Chrome renders successfully in the locked minimum Podman profile while runtime-default and
  forbidden relaxations fail closed.
- T00 local k3s `v1.35.5+k3s1` passes the target Chrome/Mermaid, network-policy, security, and
  fail-closed probes with containerd `2.2.3-k3s1`. The exact test namespace, Localhost profile, and
  imported image were removed and verified absent after the run.
- The hardened T00 k3s wrapper's offline collision, ownership-change, installed-profile-tampering,
  namespace-UID, image-digest, acquisition-failure, and proxy success/failure/interruption probes
  pass. The orchestrator found and identity-checked two argv-rewritten proxy orphans from legacy
  `reviewfix04`/`reviewfix05`, terminated only those PIDs, and verified no `kubectl` remained. The
  new regression detects argv/token disappearance and preserves a baseline `kubectl`. Live run
  `reviewfix06` passes all workload probes,
  verifies installed profile SHA-256 `bbd643f78d48b477111dd8597a69ba6bee4db68ce199dbf09d87bf90a1377f46`,
  and verifies its namespace, profile, marker, containerd image, and Podman tag absent afterward.
- Final offline regressions prove that a baseline `kubectl` carrying the same operator-supplied run
  token survives and blocks launch, and that create collisions or invalid namespace receipts
  preserve identically labeled namespaces without attempting deletion. A valid receipt still
  supports cleanup after the injected post-create API failure.
- The T00 Podman regression, Ruff, and ty checks pass. The service-independent Pytest selection
  passes 175 tests at 98% coverage; both canonical Pytest commands report the same 10 PostgreSQL/
  RustFS integration failures because this worktree has no test-service environment variables.
- Final product validation has not run; the product is incomplete.
