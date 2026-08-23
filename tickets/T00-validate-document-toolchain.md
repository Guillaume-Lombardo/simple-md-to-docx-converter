---
ticket: T00
linear_id: G1L-310
linear_url: https://linear.app/g1lom/issue/G1L-310/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T00 - Validate the UBI 9 and Python 3.14 document toolchain

## Objective

Validate UBI 9/Python 3.14, Pandoc, Chromium/Mermaid, LibreOffice, sandboxing, fonts, resource budgets, and rootless runtime through reproducible spikes.

## Acceptance criteria

- The implementation satisfies the T00 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- None

## Progress

- 2026-08-23: Reproducible toolchain validation started on `chore/T00-validate-document-toolchain`.
- 2026-08-23: Added a checksum-pinned UBI 9/Python 3.14 validation image and automated success/failure probes for Pandoc, local resources, Mermaid/Chrome, Fontconfig, LibreOffice, arbitrary UID, read-only root, no network, capabilities, writable areas, and cgroup envelopes.
- 2026-08-23: Confirmed that the public UBI repositories do not provide Pandoc, Chrome/Chromium, or LibreOffice; upstream source approval remains a product/security decision.
- 2026-08-23: Confirmed through warning and OpenXML inspection that Pandoc `--sandbox` omits local resources, and that Chrome cannot start under runtime-default seccomp plus `no-new-privileges`. OpenShift validation is PM-deferred; the committed probe uses neither `seccomp=unconfined` nor a browser no-sandbox flag.
- 2026-08-23: Review corrections now assert the claimed security properties inside the container, exercise their relevant failure probes, distinguish pinned engine inputs from mutable UBI RPM resolution, record the complete reviewed RPM inventory, and document the `/work` tmpfs versus final disk-backed-runtime gap.
- 2026-08-23: T00 remains In Progress pending an approved Chrome/OpenShift sandbox architecture, an approved engine-source policy, and validation on Podman/OpenShift. The final-image E2E criterion is deferred to T20/T21 because no final application image exists on this base revision; explicit reviewer approval is required if this is classified as an exception.
- 2026-08-23: Added primary-source decision evidence for every engine and UBI/font input, including available signatures, checksums, licenses, and update/CVE ownership choices that remain for PM/security approval.
- 2026-08-23: Added a reproducible Pandoc 3.10.2 matrix. The `commonmark_x` candidate supports tables, footnotes, YAML metadata, and image attributes, but `-raw_html` still emits raw HTML nodes and `raw_tex` is unsupported; no final dialect was selected.
- 2026-08-23: Documented supported Chromium sandbox compositions without recommending `--no-sandbox`, inventoried font candidates without approval, and passed the Docker probe with disk-backed `/work`. Podman is absent and OpenShift remains deferred, so T00 stays In Progress.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
