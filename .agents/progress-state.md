# Orchestration State

Last verified: 2026-08-24

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Verified State

- `main` delivered T00-T09, T12, and T14. T09 implementation PR #38 and completion PR #39 were
  squash-merged as `4280ca0699d1c80790aa0a7289a8ba8984c97214` and
  `bc1c6fb8a017461651cb1d1d202a2a7017754777`; their exact main runs `32710022591` and
  `32710324116` passed the protected gate. Linear G1L-319 and the local T09 mirror are `Done`.
- T10/G1L-321 is `In Progress` on `feat/T10-template-font-validation`, based on clean delivered
  main. Start commit: `874ee15`.
- T10 is the smallest ready critical-path ticket. T00 and T07 are verified dependencies; T10 blocks
  T11 and T15.
- Three read-only independent analyses cover official font artifacts, bounded OOXML architecture,
  and the security/integration test matrix. No implementation ownership overlaps.
- K3s is stopped. It will be started only for an applicable test and stopped immediately afterward.

## T10 Scope

- Pin official Liberation, Carlito, Caladea, and DejaVu artifacts and notices; record exact
  Fontconfig aliases and deterministic substitution order.
- The approved corpus currently requires Latin and Greek. Add Noto only if an approved template
  contract explicitly introduces another script not covered by the fixed families.
- Implement bounded DOCX/OOXML validation with explicit T18-owned limits; reject unsafe archives,
  macros, active content, and external relationships; require the Pandoc style contract and declared
  expected fonts.
- Before activation, verify a blank canonical Pandoc conversion and real LibreOffice opening.
- T11 owns PDF generation, T15 owns versioned API/storage mutations, and T20/T21 own final-rootless-
  image E2E. Those concerns must not be absorbed into T10.

## Approved Decisions

- Official publisher artifacts are checksum locked; signatures or attestations are verified where
  available. The fixed font families are Liberation plus Carlito/Caladea with DejaVu fallback.
- Use `commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html`; reject raw HTML
  before Pandoc. Production limits remain explicit configuration until T18.
- The standing PM authorization permits publishing, squash-merging after green CI and independent
  review, and cleaning the exact merged branch without another pause.

## Blockers and Risks

- No PM-only blocker exists.
- OpenShift compatibility remains deferred and must not be claimed.
- The full legacy document-engine suite still requires its established writable/noexec and Chrome
  sandbox harness. T10-specific rootless integration and the canonical toolchain harness pass.
- Git SSH transport is unusable on this VM; authorized GitHub publication uses the authenticated
  `gh` HTTPS credential helper without exposing or persisting tokens.

## Next Actions

1. Complete canonical checks and reconcile any failures.
2. Obtain independent security, specification, and CI review of the exact revision.
3. Rebase, publish, and squash-merge after green CI.
4. Synchronize T10/Linear to `Done`, verify main CI, then immediately select the next ready ticket.

## Validation

- T09 contains 46 Mermaid unit tests and 12 real-engine integration tests. The local applicable
  canonical selection passed 555 tests at 95.34% coverage; independent security/spec/CI reviews
  approved the exact revision and the explicit T20/T21 final-image E2E deferral.
- The delivered T09 squashes passed every applicable GitHub Actions domain and `CI / gate`.
- T10 currently adds 107 passing unit tests and 17 passing rootless real-engine integration tests.
  The unit suite passes 537 tests at 93.63% application coverage. The final T10 rootless image
  passes the T00 document-engine harness with the exact Pandoc, LibreOffice, Chromium, font,
  rootless, read-only-root, no-network, and dropped-capability contract.
- A fresh T00 K3s rerun reached an environment-specific network control failure before workload
  validation and was inconclusive; all resources were removed and K3s stopped. Previously delivered
  K3s proof remains valid and no VM networking was modified.
- Final product validation has not run; the product remains incomplete.
