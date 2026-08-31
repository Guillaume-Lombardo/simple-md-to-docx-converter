---
ticket: T56
linear_id: G1L-480
linear_url: https://linear.app/g1lom/issue/G1L-480/t56-allow-safe-https-hyperlinks-without-remote-resource-loading
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T56 - Allow safe HTTP(S) hyperlinks without remote resource loading

## Objective

Allow safe absolute HTTP(S) hyperlinks in Markdown conversions without permitting any
document-controlled remote resource fetch. Remote images and every other remote resource remain
rejected.

## Acceptance criteria

- Ordinary Markdown links and autolinks with well-formed absolute `http://` or `https://`
  destinations are accepted.
- External link destinations are validated with an explicit scheme allowlist, a required host, no
  credentials, and no control characters; unsafe, malformed, protocol-relative, or encoded-scheme
  destinations remain rejected.
- Images with HTTP(S), protocol-relative, encoded, data, file, FTP, or other remote destinations
  remain rejected before Pandoc runs.
- Pandoc continues to run without network access and does not fetch hyperlink destinations.
- DOCX OpenXML integration coverage verifies an accepted external hyperlink relationship.
- A rootless final-image E2E conversion uses an anonymized fixture derived from the reported
  document, succeeds in both storage profiles, and verifies the external hyperlink relationships
  in the downloaded DOCX.
- The product specification and user documentation distinguish clickable external hyperlinks from
  remotely loaded document resources.
- Ruff, ty, the canonical test suite, document-engine integration tests, and both-profile
  final-image E2E checks pass.

## Dependencies

- T07
- T08
- T21

## Scope

This ticket changes validation and documentation for HTTP(S) hyperlink destinations only. It does
not allow remote images, remote stylesheets, remote includes, or document-controlled network
access.

## Progress

- 2026-08-31: Started after a real Markdown conversion failed validation because ordinary HTTPS
  hyperlinks were classified as remote resources. The approved contract permits strictly validated
  HTTP(S) hyperlinks while preserving the prohibition on remote images and network fetching.
- 2026-08-31: Validation now accepts well-formed absolute HTTP(S) link destinations with a host and
  without embedded credentials or decoded control characters. Other schemes, protocol-relative and
  encoded-scheme forms, malformed ports, and all remote image destinations remain rejected. The
  attached source passes the revised validator while a remote HTTPS image fails with the stable
  validation error.
- 2026-08-31: Added an English anonymized E2E fixture derived from the reported document structure.
  Both standalone and distributed rootless final-image workflows passed, including exact OpenXML
  hyperlink relationship inspection for two non-resolving `.invalid` destinations and a terminal
  validation failure for a remote HTTPS image. Both complete workflows also passed their existing
  DOCX/PDF, CLI, browser, restart, recovery, authorization, and concurrency scenarios.
- 2026-08-31: Ruff formatting and linting, ty, 23 JavaScript tests, and 1,923 unit-domain tests pass;
  application coverage is 94.32%. The broader engine-excluded run passed 2,151 tests at 95.49%
  coverage, with only the expected 32 PostgreSQL setup errors and three RustFS failures because the
  host service variables were absent. Host Pandoc is also unavailable; the real Pandoc boundary was
  instead exercised successfully by both final-image workflows.
- 2026-08-31: PR #150 was squash-merged as
  `db88c2b8aa07657e0514cfb8f153b221dc3e127e`; CodeRabbit reported no actionable comments and the
  complete exact-main CI run 33437138553 passed. The requested patch-release closure now prepares
  version `0.5.1`. Compose intentionally remains pinned to the verified `0.5.0` digest until the
  protected release publishes `0.5.1`; the immediate follow-up will adopt the exact retained GHCR
  receipt, validate the public quickstarts, and only then mark T56 Done.
- 2026-08-31: Release PR #151 was squash-merged as
  `eac6637eeb6fcdb6b32cc1a5d10b0d7747bf27e8`. Automatic release run 33441588004 published
  `markweave 0.5.1` to PyPI, created `v0.5.1` and its GitHub Release at that exact source SHA, and
  completed the GHCR build, SBOM/vulnerability evidence, provenance attestation, and retained
  release-evidence jobs. The retained receipt records registry digest
  `sha256:c37ed02dfbf6a74e140280efcf4152e4499dcba929d2f23cab22da8414c22fec`; a separate anonymous
  manifest fetch returned the same digest and verified it against the public manifest bytes. This
  phase adopts that exact published image in Compose and validates the public quickstarts before
  completing T56.
- 2026-08-31: The exact-main CI run 33441587678 for release commit
  `eac6637eeb6fcdb6b32cc1a5d10b0d7747bf27e8` passed. The public simple quickstart then pulled the
  retained `0.5.1` digest and reached readiness with both Docker and rootless Podman. Each runtime
  completed a real template-free conversion of the anonymized external-hyperlink fixture in
  `pandoc-default` mode; the downloaded DOCX passed OpenXML validation and contained exactly the
  expected sanitized HTTP and HTTPS hyperlink relationships. The isolated containers, volumes,
  and temporary quickstart state were removed after validation.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
or progress changes.
