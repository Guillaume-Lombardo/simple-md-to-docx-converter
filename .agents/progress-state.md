# Orchestration State

Last verified: 2026-08-24

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Verified State

- `main` at `80974b3` has delivered T00-T12 and T14. T11 implementation PR #42 and completion PR
  #43 were squash-merged as `5d4d34c` and `80974b3`; exact main CI run `32722804067` passed every
  implemented domain and the protected gate. T11 is `Done` locally and in Linear.
- T13/G1L-322 is `In Progress` on `feat/T13-persistent-queue-workers`, based on `80974b3`; its T11
  and T12 dependencies are verified `Done` locally and in Linear.
- Ready pull request #44 publishes the independently approved T13 implementation. Its source branch
  was rebased on unchanged `origin/main`; required GitHub checks are pending.
- The uncommitted T13 implementation contains the durable job state machine, owner-scoped
  idempotency, authenticated conversion API, SQLite/PostgreSQL queue and Alembic schema, unique
  lease and cleanup fencing tokens, attempt-specific result publication, independent periodic
  heartbeat, continuous recovery, cancellation-wins transitions, row-first source staging,
  pre-parser request limits, retry-safe cleanup, and supervised embedded/external worker loops.
- Required production-policy values have no defaults. T15 retains the immutable template-version
  processor connection, T18 retains approved timing/quota/retention/cleanup values, and T20/T21
  retain final runtime-mode wiring and rootless-image E2E.
- K3s is stopped. The existing `t12-postgres-v2` and `t12-rustfs-v2` containers remain running and
  were used without modification for real profile tests.

## Validation

- `uv sync --all-groups`, formatting, Ruff, and `ty` pass.
- Canonical unit selection passes 655 tests at 93.55% overall application coverage and more than
  90% branch coverage.
- Canonical default selection passes 805 tests at 94.73% overall coverage, including real
  PostgreSQL, RustFS, SQLite restart, filesystem, ASGI, real workers across both profiles,
  concurrent idempotency and claims, stale fencing, periodic heartbeat, cancellation races,
  result publication, source recovery, and cleanup retry/ownership.
- The unfiltered host suite passes 809 tests. Its 34 failures are the established marked
  document-engine tests because Pandoc, Mermaid/Chromium, LibreOffice, and locked fonts are absent
  from the VM PATH after the T11 image cleanup; no T13 test fails.
- Final independent specification and security reviews approve the exact revised tree. The final
  test review approves it after the mechanical validation-count correction now applied. All three
  explicitly approve the documented T20/T21 final-image E2E sequencing exception as sequencing,
  not a waiver.
- Linear G1L-322 remains aligned as `In Progress`. Final product validation has not run;
  the product remains incomplete.

## Approved Decisions

- Standing PM authorization permits ready PR publication, validated squash merge, and exact branch
  cleanup without another routine pause.
- Production limits remain explicit configuration until T18. No OpenShift compatibility claim is
  made. T20/T21 sequencing is not treated as executed E2E coverage.
- Git SSH transport is unusable on this VM; authorized GitHub publication uses the authenticated
  `gh` HTTPS credential helper without exposing or persisting tokens.

## Blockers and Risks

- No PM-only blocker exists.
- The final-image E2E acceptance debt requires explicit reviewer approval in the T13 PR because the
  final image and process-mode wiring are owned by T20/T21.
- The real document processor cannot be assembled until T15 provides immutable template-version
  content resolution; T13 tests the worker through its explicit processor port.

## Next Actions

1. Require green CI on PR #44, then squash-merge under standing authorization.
2. Verify the squash on `main`, synchronize T13 to `Done`, clean the
   exact branch, and immediately continue the next ready critical-path ticket.
