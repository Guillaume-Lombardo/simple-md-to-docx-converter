# Orchestration State

Last verified: 2026-08-24

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Verified State

- `main` is clean and matches `origin/main`; delivered history through documentation sync PR #28
  is verified below.
- T01, T02, T03, T05, T06, T12, and T14 are delivered. T00 remains `In Progress`; T04 remains
  dependent on completion of T00 evidence.
- PR #26 delivered the independently reviewed rootless Podman Chrome sandbox proof. The sandbox
  stays enabled, `--no-sandbox` is forbidden, k3s validation is next, and OpenShift proof remains
  deferred.
- PR #27 delivered the independently approved T14 ownership, visibility, search, preference, and
  fallback foundations as squash `c296d458b2d64c3ee1d9cfbb6f65e8f86ff440b9`. The local ticket
  and Linear G1L-325 are fully synchronized as `Done`.
- PR #28 delivered the approved T00 scope and synchronized orchestration and ticket state as squash
  `b2e5b3965ad05448c56d6fd857191489f8a94173`; its main validation passed the protected gate.
- The PM-authorized local k3s T00 validation passed with the checksum-locked Chrome seccomp profile
  and fail-closed controls. The exact namespace, Localhost profile, and imported image were removed
  and verified absent. The cluster is no longer needed, and the orchestrator stopped k3s.
- T00 review blockers are corrected locally: cluster-global names are unique and collision-checked,
  installed profile integrity is verified, cleanup is ownership- and UID-preconditioned, and offline
  negative probes cover collision and tampering refusal. The hardened live rerun passed and removed
  every exact run resource; publication and independent approval remain pending.

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
  envelopes, with the remaining sandbox/runtime gaps stated explicitly.

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

1. Commit the corrected T00 k3s proof, publish it only after explicit approval, obtain independent
   approval, and verify it on `main`; then synchronize T00/Linear and unblock T04. OpenShift
   validation remains deferred.
2. Re-read Linear and select only a ready ticket whose dependencies are verified `Done`; T04 still
   waits for T00 and T13 still waits for T11.
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
- Linear G1L-325 was fetched by identifier and reports `Done`, with the PR #27 attachment and exact
  delivery entry matching the local `Done` ticket.
- Linear G1L-310 was fetched by identifier and matches the local title, project, team, priority,
  status, acceptance criteria, dependencies, and all local progress entries, including PR #26 and
  the approved scope allocations and exclusions. It remains `In Progress`.
- T00 Docker and rootless Podman harnesses pass tmpfs, disk-backed, security, and failure probes;
  Chrome renders successfully in the locked minimum Podman profile while runtime-default and
  forbidden relaxations fail closed.
- T00 local k3s `v1.35.5+k3s1` passes the target Chrome/Mermaid, network-policy, security, and
  fail-closed probes with containerd `2.2.3-k3s1`. The exact test namespace, Localhost profile, and
  imported image were removed and verified absent after the run.
- The hardened T00 k3s wrapper's offline collision, ownership-change, installed-profile-tampering,
  namespace-UID, and image-digest probes pass. Live run `reviewfix05` passes all workload probes,
  verifies installed profile SHA-256 `bbd643f78d48b477111dd8597a69ba6bee4db68ce199dbf09d87bf90a1377f46`,
  and verifies its namespace, profile, marker, containerd image, and Podman tag absent afterward.
- The T00 Podman regression, Ruff, and ty checks pass. The service-independent Pytest selection
  passes 175 tests at 98% coverage; both canonical Pytest commands report the same 10 PostgreSQL/
  RustFS integration failures because this worktree has no test-service environment variables.
- Final product validation has not run; the product is incomplete.
