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

- 2026-08-23: A checksum-pinned UBI 9/Python 3.14 validation image and automated success/failure probes now cover Pandoc, local resources, Mermaid/Chrome, Fontconfig, LibreOffice, arbitrary UID, read-only root, no network, capabilities, writable areas, and cgroup envelopes.
- 2026-08-23: Public UBI repositories do not provide Pandoc, Chrome/Chromium, or LibreOffice; upstream source approval remains a product/security decision.
- 2026-08-23: Pandoc `--sandbox` omits local resources according to both its warning and OpenXML inspection. Chrome cannot start under runtime-default seccomp plus `no-new-privileges`. OpenShift validation is PM-deferred; the committed probe uses neither `seccomp=unconfined` nor a browser no-sandbox flag.
- 2026-08-23: Review corrections assert the claimed security properties inside the container and exercise their relevant failure probes, distinguish pinned engine inputs from mutable UBI RPM resolution, record the complete reviewed RPM inventory, and document the `/work` tmpfs versus final disk-backed-runtime gap.
- 2026-08-23: T00 remains In Progress pending an approved Chrome/OpenShift sandbox architecture, an approved engine-source policy, and Podman/OpenShift validation. Final-image E2E is deferred to T20/T21 because the final application image does not exist yet; explicit reviewer approval is required if this is classified as an exception.
- 2026-08-23: GitHub PR #4 was independently reviewed, squash-merged into `main` as `1fd8bf06e78d677a1d45d09950f8bf12548acb05`, and verified with the published T00 evidence intact. T00 remains In Progress for the recorded deferred decisions.
- 2026-08-23: Added primary-source decision evidence for every engine and UBI/font input, including available signatures, checksums, licenses, and update/CVE ownership choices that remain for PM/security approval.
- 2026-08-23: Added a reproducible Pandoc 3.10.2 matrix. The `commonmark_x` candidate supports tables, footnotes, YAML metadata, and image attributes, but `-raw_html` still emits raw HTML nodes and `raw_tex` is unsupported; no final dialect was selected.
- 2026-08-23: Documented candidate Chromium sandbox compositions without recommending `--no-sandbox`, inventoried font candidates without approval, and passed the Docker probe with disk-backed `/work`. Podman is absent and OpenShift remains deferred, so T00 stays In Progress.
- 2026-08-23: PM approved official publisher artifacts with available signature/provenance verification and locked integrity; Pandoc SHA-256 is accepted when no detached signature exists. Vulnerabilities are reviewed weekly and Critical findings receive urgent handling.
- 2026-08-23: PM approved `commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html` with mandatory raw-HTML rejection before Pandoc; Chromium keeps its sandbox and must never use `--no-sandbox`; the minimum seccomp/user-namespace profile is validated on rootless Podman and then k3s, while OpenShift proof is deferred.
- 2026-08-23: PM approved Liberation plus Carlito/Caladea, DejaVu fallback, and Noto only for explicitly required scripts, and authorized system Podman installation on the development VM. T00 remains In Progress until the approved rootless sandbox path and remaining evidence are implemented and verified.
- 2026-08-23: Podman 5.4.2 now runs the harness rootless on Debian with `runc`, cgroup v2, sparse arbitrary-UID/GID mappings, explicit bounded writable mounts, and no security relaxation. Its tmpfs and disk-backed Pandoc/Fontconfig/LibreOffice probes and all expected failure probes pass; the Docker regression suite also passes. Chrome still fails safely before Mermaid rendering at the Podman zygote `sys_chroot` step and at namespace creation with `EPERM` under Docker. The exact pinned UBI base required a one-time preload into Podman's separate store because the harness retains `--pull=false`. T00 remains In Progress for the minimal sandbox profile, k3s, deferred OpenShift proof, production signature enforcement, exact font/substitution evidence, and production limits.
- 2026-08-23: Started a focused rootless Podman Chrome sandbox spike to reproduce the zygote `sys_chroot` failure and test the smallest supported user-namespace/seccomp composition without weakening arbitrary-UID, read-only-root, no-network, no-capability, no-new-privileges, bounded-write, or cgroup constraints. T00 remains In Progress.
- 2026-08-23: Rootless Podman now renders Mermaid with Chrome's namespace, PID/network namespace, and Seccomp-BPF layers active. The checksum-locked portable profile is containers/common 0.62.2 runtime-default seccomp with only its capability-conditioned `chroot` pair replaced by one allow rule; the outer container still has UID `1000710000`, zero capabilities, `NoNewPrivs=1`, seccomp filter mode, read-only root, network `none`, bounded writable areas, and cgroups. Runtime-default reproduces the original zygote failure, profile tampering and forbidden security relaxations fail closed, and both the full Podman harness and Docker regression pass. T00 remains In Progress for k3s validation, deferred OpenShift proof, production signature enforcement, exact font/substitution evidence, and production limits.
- 2026-08-23: GitHub PR #26 passed run `32668812723`, was squash-merged into `main` as `4758cbf7682ea815e797e78b871384247a72f884`, and that exact merge passed main run `32668864601`, including `CI / gate`. The rootless Podman Chrome sandbox proof is therefore verified on `main`; k3s validation is next, while OpenShift proof remains explicitly deferred.
- 2026-08-23: The PM assigned exact font artifacts, licenses, Fontconfig substitutions, and script-specific Noto evidence to T10. Production limits, RPO/RTO, retention, quotas, antivirus integration, and cleanup remain configurable until T18. PDF/A output and automatic Word/PDF table-of-contents generation are outside the initial product scope. These decisions narrow T00's remaining work without marking it complete.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
