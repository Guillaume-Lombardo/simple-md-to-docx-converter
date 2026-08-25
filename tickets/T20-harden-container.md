---
ticket: T20
linear_id: G1L-330
linear_url: https://linear.app/g1lom/issue/G1L-330/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T20 - Build and harden the final container image

## Objective

Build the reproducible rootless image with API and worker modes, SBOM, scans, and smoke tests.

## Acceptance criteria

- The implementation satisfies the T20 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.
- The final image supports both storage profiles and uses the provider-neutral AWS S3-compatible object-store contract; distributed-profile test deployments use RustFS, never MinIO.
- The hardened final image is used to discharge the rootless E2E debt explicitly deferred from T12, with explicit reviewer approval of the sequencing exception.
- The final image includes the T11 PDF adapter and locked document engines so T21 can discharge
  deferred end-to-end PDF success, failure, cancellation, concurrency, and asynchronous-workflow
  coverage.

## Dependencies

- T11
- T12
- T13
- T18

## Progress

- 2026-08-23: Scope now records the T12 sequencing debt: build the final rootless image for both profiles so T21 can verify the deferred storage workflows. RustFS is the CI/k3s S3-compatible implementation; the application contract remains provider-neutral and AWS S3-compatible.
- 2026-08-24: Scope now also records T11's approved sequencing debt. The final image must carry the
  verified Pandoc, LibreOffice, PDFium test, and font contracts needed for T21's final asynchronous
  PDF workflow E2E.
- 2026-08-24: Started implementation on `feat/T20-harden-container` from verified `main` at
  `375abd7` after T11, T12, T13, and T18 were confirmed `Done`. This workstream owns the final image,
  rootless runtime modes, build/deployment assets, SBOM and vulnerability scans, smoke/container
  tests, and operational documentation. T19 owns application source observability and readiness;
  T20 will report rather than edit `src/md_converter/**` if an application change is required.
- 2026-08-24: Built the final UBI 9/Python 3.14 image twice with an identical image ID and verified
  its rootless, read-only, capability-free runtime plus Pandoc, Mermaid/Chromium, and LibreOffice
  conversions. The distributed PostgreSQL/RustFS API smoke test passes. CycloneDX and SPDX SBOMs
  were generated, and the fixed-Critical Grype gate passes after removing build-only npm tooling.
  The canonical non-engine suite passes all 997 applicable tests against real PostgreSQL and RustFS
  with 95.06% coverage; Ruff, ty, ShellCheck, and CI configuration validation also pass. The
  initial standalone startup and package-native runtime gaps were carried forward for resolution
  after T19 integration.
- 2026-08-25: Integrated verified T19 `main` at `3056736`, implemented all 14 SQLite 3.34 write
  fallbacks while retaining PostgreSQL `RETURNING`, and assembled the production template-aware
  processor plus embedded and external worker runtimes. Guarded SQLite tests reject any remaining
  `UPDATE ... RETURNING` execution. The final rootless image
  `1711b7d8762f06b907def197987aba7646869ac04246b3a69058c7e153229e18` is reproducible and passes
  standalone SQLite/filesystem and distributed PostgreSQL/RustFS smoke tests, including external
  worker metrics and graceful termination. CycloneDX and SPDX SBOMs were generated; Grype reports
  28 findings (8 High, 18 Medium, 2 Low) and zero Critical findings, so the fixed Critical gate
  passes without suppressions or threshold changes.
- 2026-08-25: Addressed independent review findings by freezing and validating the source filename,
  kind, SHA-256 digest, and byte size; failing historical non-terminal rows closed; publishing PDF
  traceability manifests atomically beside results; retaining complete Grype evidence while gating
  fixable Critical findings; and exercising authenticated DOCX, PDF, and combined conversions in
  both final-image storage profiles. The standalone and distributed rootless workflows pass. The
  canonical non-engine suite passes all 1,147 tests against real PostgreSQL and RustFS with 95.32%
  coverage. Two fixed-timestamp builds produced image ID
  `23fe74cb43abc0a6acd7af8a8f21a16c13995ddb028dcdbdd4a38ea18156635e` and digest
  `sha256:f12363b0ed23ceab4ca380fdc345b3925cfc0c5de6e7825fa5b2b10260d66601`.
  The complete 1,502-match Grype report contains no Critical findings; the host-only full suite
  cannot run its 37 document-engine tests because those binaries and locked fonts are unavailable
  outside the final image, where the corresponding real workflows pass.
- 2026-08-25: Resolved the remaining review findings. Active SIGTERM now propagates through the
  worker cancellation probe to Pandoc and Mermaid process groups, preserves durable cancellation
  and maximum-duration precedence, and leaves an interrupted lease recoverable without publishing
  objects. Revision 12 now uses a trigger-preserving SQLite 3.34 table copy on downgrade, with real
  SQLite and PostgreSQL upgrade-downgrade-upgrade coverage. Frozen Markdown rejects only actual ZIP
  signatures, and result publication enforces the DOCX/PDF/BOTH manifest cardinality contract before
  object writes. Combined-output smoke validation now inspects the inner DOCX, PDF, and canonical
  manifest. The rebuilt rootless image
  `350fc58f93eb252cb33b3e35d4c7fd71cc81a55259d0f3f2856ca30871d4a72b` passes the distributed
  workflow, active blocking-Mermaid SIGTERM, orphan check, durable running-state check, lease
  recovery, and final DOCX validation. Ruff, ty, 162 focused tests, 136 final regression tests, and
  the dedicated PostgreSQL migration parity test pass.
- 2026-08-25: Re-ran final evidence on the reviewed head. The standalone authenticated final-image
  smoke passes DOCX, PDF, combined output, and source-integrity failure paths; the rootless,
  read-only runtime smoke also passes. Correct-environment PostgreSQL/RustFS runs pass all 1,164
  applicable tests both with and without coverage (44 engine tests deselected), with 95.10% total
  coverage. The run exposed and corrected one distributed PDF test double that had not supplied
  the newly mandatory traceability manifest; the real boundary test now downloads, verifies, and
  cleans up its S3 sidecar. Two fixed-timestamp builds produce identical image ID
  `350fc58f93eb252cb33b3e35d4c7fd71cc81a55259d0f3f2856ca30871d4a72b` and digest
  `sha256:07512e38f8f13ccb421574f7cc9b10e0654ca18f653760977faf07a9457d923b`. The complete current
  Syft/Grype evidence contains 1,502 matches (91 High, 723 Medium, 679 Low, 9 Negligible), with zero
  fixable and zero unfixed Critical findings. Ruff formatting and linting, ty, Bash syntax,
  error-severity ShellCheck, 113 CI/container/web checks, and the container asset contracts pass.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
