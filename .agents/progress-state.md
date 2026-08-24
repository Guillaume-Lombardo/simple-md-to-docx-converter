# Orchestration State

Last verified: 2026-08-24

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Verified State

- `main` at `74dcba5` has delivered T00-T14. T13 implementation PR #44 and completion PR #45 were
  squash-merged after green CI and independent review; their exact local and remote branches were
  removed. T13/G1L-322 is `Done` locally and in Linear.
- T15/G1L-323 is the next ready critical-path ticket. It is `In Progress` on
  `feat/T15-versioned-template-api`, based on delivered main `74dcba5`; dependencies T10 and T14 are
  verified `Done` locally and in Linear.
- The durable job API and queue support SQLite/PostgreSQL, owner-scoped idempotency, unique lease
  and cleanup fencing, periodic heartbeat and recovery, cancellation-wins transitions, safe source
  staging and result publication, request bounds, retry-safe cleanup, and embedded/external workers.
- T15 owns immutable template mutation, download, audit, and processor-version resolution. Required
  production-policy values remain configurable for T18. Pending publication cleanup now uses
  expiring ownership tokens and atomic fencing across replicas; worker assembly always injects the
  exact frozen template version. T20/T21 retain final application-image E2E.
- K3s is stopped. The existing `t12-postgres-v2` and `t12-rustfs-v2` containers remain running and
  were used without modification for real profile tests.

## Validation

- `uv sync --all-groups`, formatting, Ruff, and `ty` pass.
- The T15 correction tree passes 699 unit tests with the exact 90% branch threshold, 860 applicable
  host tests with live PostgreSQL/RustFS, 20 repeated SQLite submission/mutation race runs, and the
  3 T15 activation/publication tests in the arbitrary-UID rootless toolchain image. K3s remained
  inactive. Changed-line coverage is pending the committed-tree check.
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
- T15 must preserve T14 template identity and authorization while introducing immutable versions,
  optimistic concurrency, object cleanup, and audit parity across both storage profiles.

## Next Actions

1. Implement and validate T15 versioned template contracts on both storage profiles.
2. Commission independent specification, security, and test review before publishing a ready PR.
