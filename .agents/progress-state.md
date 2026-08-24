# Orchestration State

Last verified: 2026-08-24

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Current State

- `main` at `201116a` contains T00-T15. T15 implementation PR #46 was squash-merged after green CI
  and independent specification, security/concurrency, and test/CI approval; its exact local and
  remote source branch was removed.
- T15/G1L-323 is `Done` in Linear. Its local completion mirror is being published on
  `docs/T15-close-versioned-template-api` before the next critical-path ticket starts.
- T15 provides immutable template versions, optimistic concurrency, owner-scoped download and
  audit behavior, fenced pending-publication recovery, integrity checks, and frozen template
  resolution in production worker assembly across SQLite/filesystem and PostgreSQL/S3 profiles.
- The pending-publication lease duration is required configuration and rejects non-positive or
  non-finite values at both configuration and policy boundaries.
- K3s remains inactive. The existing `t12-postgres-v2` and `t12-rustfs-v2` containers are running
  and were used without modification.

## T15 Validation

- `uv sync --all-groups`, Ruff formatting and linting, and `ty` pass.
- The exact CI unit selection passes 699 tests. Application branch coverage is 90.05%
  (1,041/1,156), and changed application line coverage against `74dcba5` is 91.95% (811/882).
- The applicable live host selection passes 860 tests with PostgreSQL and RustFS; its focused
  PostgreSQL/S3 boundary selection passes 13 tests.
- The exact three T15 activation/publication tests pass in the rootless toolchain image under an
  arbitrary UID, read-only root and worktree, disabled network, dropped capabilities, and
  `no-new-privileges`.
- The unfiltered host suite passes 867 tests and has 37 expected marked engine failures because
  Pandoc, Mermaid/Chromium, LibreOffice, and locked fonts are intentionally absent from the host.
  Engine-bound behavior is validated in the toolchain image instead.
- Historical T13 validation counts are intentionally omitted; all counts above describe the
  current T15 tree.

## Remaining Scope and Risks

- T20/T21 still own final application-image E2E coverage; the toolchain-image run does not claim
  that later acceptance gate.
- No PM-only implementation blocker is known. Production policy values remain explicit
  configuration for T18.

## Next Action

Merge the T15 completion synchronization after its required checks, then select and start T16,
whose T13, T14, and T15 dependencies are verified delivered.
