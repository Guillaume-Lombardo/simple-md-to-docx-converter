# Orchestration State

Last verified: 2026-08-24

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Verified State

- `main` is clean at `4758cbf7682ea815e797e78b871384247a72f884` and matches `origin/main`.
  Main CI run `32668864601` passed.
- T01, T02, T03, T05, T06, and T12 are delivered. T00 remains `In Progress`; T04 remains dependent
  on completion of T00 evidence.
- PR #26 delivered the independently reviewed rootless Podman Chrome sandbox proof. The sandbox
  stays enabled, `--no-sandbox` is forbidden, k3s validation is next, and OpenShift proof remains
  deferred.
- T14 head `ea14946bb05966f29858c212f0d6184e1475bbdf` on
  `feat/T14-template-ownership` implements both prior review corrections: authenticated-owner
  derivation and real cross-profile authorization coverage. Independent re-review and checks remain
  pending; no T14 pull request is open.

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
- Reproducible T00 compatibility evidence for Pandoc, Mermaid/Chrome, Fontconfig, LibreOffice,
  arbitrary UID, read-only root, no network, dropped capabilities, writable areas, and cgroup
  envelopes, with the remaining sandbox/runtime gaps stated explicitly.

## Blockers and Risks

- Chrome/Mermaid still requires k3s validation. OpenShift compatibility cannot be claimed until the
  deferred target proof runs.
- Exact font artifacts/substitution order and explicit Noto scripts remain T10 work.
- Production limits, RPO/RTO, retention, quotas, antivirus, and cleanup remain configurable T18
  work. GitHub Actions heavy-job timeouts, full-suite frequency, and usage budget remain T22 work.
- Git SSH transport is not usable on this VM because the configured identity resolves to the public
  `~/.ssh/codex-dev.pub` file. Read-only `gh` API access works; future Git transport must use an
  approved authenticated path without exposing credentials or silently changing SSH configuration.
- GitHub merge queue is unavailable for this user-owned public repository, so merges remain
  serialized by the orchestrator.

## Next Actions

1. Continue T00 with the k3s proof; keep OpenShift proof deferred and do not weaken the browser
   sandbox.
2. Independently re-review and check exact T14 head
   `ea14946bb05966f29858c212f0d6184e1475bbdf` before publication. T13 still waits for T11 and T04
   still waits for T00.
3. Preserve the explicit T12 final-image rootless E2E debt in T20/T21.
4. Reconstruct repository, Linear, CI, and worker truth after each transition and rewrite this file
   as current state rather than a chronology.

## Validation

- PR #26 head `329fd96631b13f2cb13180fcad7176e9697e24f7` passed run `32668812723`;
  squash `4758cbf7682ea815e797e78b871384247a72f884` passed main run `32668864601`,
  including the protected `CI / gate`.
- Linear G1L-310 was fetched by identifier and matches the local title, project, team, priority,
  status, acceptance criteria, dependencies, and all local progress entries, including PR #26 and
  the approved scope allocations and exclusions. It remains `In Progress`.
- T00 Docker and rootless Podman harnesses pass tmpfs, disk-backed, security, and failure probes;
  Chrome renders successfully in the locked minimum Podman profile while runtime-default and
  forbidden relaxations fail closed.
- Final product validation has not run; the product is incomplete.
