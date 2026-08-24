# Orchestration State

Last verified: 2026-08-24

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Verified State

- `main` delivered T00-T10, T12, and T14. T10 implementation PR #40 was squash-merged as
  `02e7cc2739d4120260f2c7795b260eeec7c77d66`; its exact main run `32716429800` passed every
  implemented domain and the protected gate. T09 implementation PR #38 and completion PR #39 were
  squash-merged as `4280ca0699d1c80790aa0a7289a8ba8984c97214` and
  `bc1c6fb8a017461651cb1d1d202a2a7017754777`; their exact main runs `32710022591` and
  `32710324116` passed the protected gate. Linear G1L-319 and the local T09 mirror are `Done`.
- T10/G1L-321 is delivered on `main`; its local and Linear status are `Done`.
- T11 is the smallest ready critical-path ticket. Its T09 and T10 dependencies are verified `Done`;
  it blocks T13.
- T11/G1L-320 is `In Progress` on `feat/T11-pdf-conversion`, based on delivered main
  `c43b810`; T09 and T10 are verified dependencies.
- T11 now has an isolated LibreOffice adapter, explicit DOCX/PDF bounds, process-group timeout and
  cancellation, strict PDF validation, canonical traceability, locked PDFium rasterization, and a
  reproducible exact golden. The canonical unit selection passes 603 tests at 93.99% coverage, the
  default local suite passes 744 tests at 95.05% coverage, and the hardened rootless UBI harness
  passes all 20 real Pandoc/LibreOffice/PDFium tests.
- K3s is stopped. It will be started only for an applicable test and stopped immediately afterward.

## Delivered T10 Scope

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

1. Finish changed-line coverage and independent security/specification/CI re-review on the exact
   hardened revision.
2. Rebase, publish, verify protected CI, squash-merge, synchronize T11 to `Done`, and continue T13.

## Validation

- T09 contains 46 Mermaid unit tests and 12 real-engine integration tests. The local applicable
  canonical selection passed 555 tests at 95.34% coverage; independent security/spec/CI reviews
  approved the exact revision and the explicit T20/T21 final-image E2E deferral.
- The delivered T09 squashes passed every applicable GitHub Actions domain and `CI / gate`.
- T10 currently adds 116 passing unit tests and 18 passing rootless real-engine integration tests.
  The unit suite passes 546 tests at 93.74% application coverage. The final T10 rootless image
  passes the T00 document-engine harness with the exact Pandoc, LibreOffice, Chromium, font,
  rootless, read-only-root, no-network, and dropped-capability contract.
- Independent security, specification, and CI/toolchain reviewers approved exact code revision
  `153bca4` without a remaining actionable finding.
- A fresh T00 K3s rerun reached an environment-specific network control failure before workload
  validation and was inconclusive; all resources were removed and K3s stopped. Previously delivered
  K3s proof remains valid and no VM networking was modified.
- Final product validation has not run; the product remains incomplete.
- T11 validation: formatting, Ruff, and `ty` pass; the unit selection passes 603 tests at 93.99%
  application coverage; the default local selection passes 744 tests at 95.05%; and the rootless
  T11 boundary suite passes 20 tests including exact raster golden, concurrent profiles, failure
  outputs, timeout, cancellation, probe failure, and descendant cleanup. The unfiltered host run
  passes 751 tests and has 34 expected missing-engine failures because Pandoc, LibreOffice,
  Mermaid/Chromium, and the locked Fontconfig inventory are provided by the approved image rather
  than the VM host.
