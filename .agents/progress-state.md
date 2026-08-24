# Orchestration State

Last verified: 2026-08-24

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Verified State

- `main` at `bb5c1d0` has delivered T00-T14. T13 implementation PR #44 was squash-merged after
  independent specification, security, and test approval; its exact local and remote branch was
  removed.
- T13/G1L-322 is complete in the repository. Its completion mirror and Linear state are being
  synchronized on `docs/T13-close-persistent-queue` before the next critical-path ticket starts.
- The durable job API and queue support SQLite/PostgreSQL, owner-scoped idempotency, unique lease
  and cleanup fencing, periodic heartbeat and recovery, cancellation-wins transitions, safe source
  staging and result publication, request bounds, retry-safe cleanup, and embedded/external workers.
- Required production-policy values remain configurable for T18. T15 retains immutable
  template-version processing, while T20/T21 retain final runtime wiring and rootless-image E2E.
- K3s is stopped. The existing `t12-postgres-v2` and `t12-rustfs-v2` containers remain running and
  were used without modification for real profile tests.

## Validation

- `uv sync --all-groups`, formatting, Ruff, and `ty` pass.
- Canonical unit selection passed 655 tests at 93.55% overall application coverage and more than
  90% branch coverage.
- Canonical default selection passed 805 tests at 94.73% overall coverage, including real
  PostgreSQL, RustFS, SQLite restart, filesystem, ASGI, real workers across both profiles,
  concurrent idempotency and claims, stale fencing, periodic heartbeat, cancellation races,
  result publication, source recovery, and cleanup retry/ownership.
- The previous unfiltered host suite passed 809 tests. Its 34 failures were the established marked
  document-engine tests because Pandoc, Mermaid/Chromium, LibreOffice, and locked fonts are absent
  from the VM PATH after the T11 image cleanup; no T13 test fails.
- Final independent specification and security reviews approve the exact revised tree. The final
  test review approves it after the mechanical validation-count correction now applied. All three
  explicitly approve the documented T20/T21 final-image E2E sequencing exception as sequencing,
  not a waiver.
- GitHub Actions run `32729784702` passed all eight jobs for exact reviewed head `b41e1aa`, including
  the real document-engine suite and final gate. Final product validation has not run; the product
  remains incomplete.

## Approved Decisions

- Standing PM authorization permits ready PR publication, validated squash merge, and exact branch
  cleanup without another routine pause.
- Production limits remain explicit configuration until T18. No OpenShift compatibility claim is
  made. T20/T21 sequencing is not treated as executed E2E coverage.
- Git SSH transport is unusable on this VM; authorized GitHub publication uses the authenticated
  `gh` HTTPS credential helper without exposing or persisting tokens.

## Blockers and Risks

- No PM-only blocker exists.
- The T13 final-image E2E sequencing debt was explicitly approved by all three independent reviewers
  and remains owned by T20/T21.
- The real document processor cannot be assembled until T15 provides immutable template-version
  content resolution; T13 tests the worker through its explicit processor port.

## Next Actions

1. Merge this completion synchronization after its required checks and verify T13 `Done` locally
   and in Linear.
2. Select and start the smallest ready critical-path ticket without pausing for routine approval.
