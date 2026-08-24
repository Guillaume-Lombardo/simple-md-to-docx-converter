---
ticket: T09
linear_id: G1L-319
linear_url: https://linear.app/g1lom/issue/G1L-319/
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T09 - Render Mermaid with local Chromium

## Objective

Render Mermaid through local Chromium under arbitrary UID, bounded resources, rootless constraints, and no network.

## Acceptance criteria

- The implementation satisfies the T09 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- T00
- T08

## Progress

- 2026-08-24: T00 and T08 are verified `Done` in the repository and Linear. Implementation started
  on `feat/T09-local-mermaid`. Scope is local Mermaid CLI/Chromium rendering, deterministic diagram
  replacement before Pandoc, bounded source/output/dimensions/process execution, stable failures,
  and rootless/no-network integration evidence. T10 fonts, T11 PDF, T13 asynchronous cancellation,
  T18 production limit values, and T20/T21 final-image E2E remain outside this ticket.
- 2026-08-24: The Mermaid preprocessor, bounded CLI adapter, Pandoc composition, stable failures,
  CI engine provisioning, documentation, 46 unit tests, and 12 real-engine integration tests are
  implemented. The real suite passes in the T00 UBI image with arbitrary UID, read-only root,
  zero capabilities, `no-new-privileges`, the approved Chrome seccomp profile, bounded resources,
  active Chrome sandbox, and `--network none`.
- 2026-08-24: Two fresh local k3s harness runs cleaned up safely but were inconclusive because the
  unselected network-control pod could not reach its Service, including after the node and CoreDNS
  reported ready. The Mermaid/Chrome target pod did not report a failure before that control gate.
  K3s was stopped and verified free of test namespaces, seccomp profiles, proxies, and containers.
  No VM network configuration was changed; the established T00 k3s sandbox proof remains valid.
- 2026-08-24: Independent review findings were resolved by pinning Node 22.23.1 in CI, enforcing
  raw and normalized output limits, using one no-follow/nonblocking output descriptor, bounding the
  final process reap, prevalidating all generated paths before rendering, adding real failure and
  descendant-cleanup tests, and checking landscape and portrait OpenXML extents. The reviewer
  explicitly approved deferring final-image E2E to T20/T21.
- 2026-08-24: GitHub PR #38 exact head `ab5a4ff972964d7435d3dc4d7e727dd8645560cd`
  passed run `32709864900`, including the document-engine domain and protected gate, and was
  squash-merged into `main` as `4280ca0699d1c80790aa0a7289a8ba8984c97214`. That exact squash
  passed main run `32710022591`, including the protected gate. T09 is delivered and verified on
  `main`; final-image E2E remains explicitly assigned to T20/T21 and OpenShift proof remains
  deferred without a compatibility claim.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
