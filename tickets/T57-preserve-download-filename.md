---
ticket: T57
linear_id: G1L-521
linear_url: https://linear.app/g1lom/issue/G1L-521/t57-preserve-uploaded-filename-for-conversion-downloads
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T57 - Preserve uploaded filename for conversion downloads

## Objective

Preserve the uploaded source filename stem when naming a completed conversion download.

## Acceptance criteria

- Result download filenames use the persisted upload filename stem and the extension selected by
  the conversion output.
- Standalone `.md` uploads and `.zip` uploads are covered.
- Filenames remain safe for an HTTP `Content-Disposition` attachment header, including non-ASCII
  and punctuation cases.
- DOCX, PDF, and combined ZIP results are covered by automated tests.
- Existing authorization, cache-control, and `nosniff` download behavior is preserved.
- Relevant unit, functional, browser, and final-image E2E assertions are updated.
- The canonical formatting, linting, type-checking, and applicable test commands pass.

## Dependencies

- T16
- T21

## Progress

- 2026-09-01: Created Linear issue G1L-521 and this repository mirror after confirming that no
  existing ticket covered result filename preservation. Implementation started on
  `fix/T57-preserve-download-filename` from verified `main` at `d2ec17d`.
- 2026-09-01: Implemented source-stem result names for DOCX, PDF, and combined ZIP downloads with
  RFC 5987 encoding for names that are not safe ASCII quoted strings. Legacy retained jobs without
  source metadata keep the previous `conversion-<job-id>` fallback. Updated the conversion and API
  guides, the normative product specification, plus unit, functional, real-browser, and final-image
  E2E expectations.
- 2026-09-01: `uv sync --all-groups`, Ruff format/check, `ty`, `uv lock --check`, CI validation,
  40 targeted unit/functional tests, 12 documentation/E2E-harness tests, and 23 native JavaScript
  tests pass. The applicable canonical Pytest run reached 91.41% application branch coverage and
  passed 2,155 tests; 32 PostgreSQL setup errors and 3 RustFS failures remain because their required
  environment and services are unavailable. Real-browser and final-image execution remain
  unverified locally because Chrome and the final service environment are unavailable.
- 2026-09-01: Ready PR #154 exposed that the deterministic Chrome test service did not copy the
  admitted source integrity metadata into its in-memory job, so the download correctly used the
  legacy fallback while the new browser assertion expected `source.docx`. The harness now preserves
  the request filename, kind, digest, and size like production persistence before CI is rerun.
- 2026-09-01: Addressed all four CodeRabbit review threads: documented the legacy filename fallback
  and fixed result media type, taught the final-image assertion to decode RFC 5987 filenames, added a
  non-ASCII filename journey, and rejected empty sources at the job request boundary. Ruff, `ty`, 23
  JavaScript tests, and the 1,929-test unit gate pass with 94.33% total application coverage.
- 2026-09-01: PR #154 was squash-merged as
  `b141b9e114b31433a3dc4bbcf5ccd25670effcd7`; CodeRabbit resolved every review thread and exact-main
  CI run 33483876501 passed the complete light, document-engine, both-profile E2E, functional, and
  storage matrix. The requested patch-release closure now prepares version `0.5.2`. Compose remains
  pinned to the verified `0.5.1` digest until publication produces the exact retained GHCR receipt.
- 2026-09-01: Release PR #156 was squash-merged as
  `8e9ef63f62e81012cc31346acb7f3a390da6863d`. Automatic release run 33508555975 published
  `markweave 0.5.2` to PyPI, created `v0.5.2` and its GitHub Release at that exact source SHA,
  and completed the GHCR build, SBOM and vulnerability evidence, provenance attestation, and
  retained release-evidence jobs. The retained receipt records registry digest
  `sha256:7d6c69ff76004bf1db6781eeec49fadac9633dbc3d8725e19060b67538fc8d8e`;
  the downloaded receipt and metadata matched their GitHub asset digests, and a separate anonymous
  manifest fetch returned the same digest in both its response header and the hash of the exact
  public manifest bytes. This phase adopts that exact published image in Compose before public
  quickstart validation and ticket completion.
- 2026-09-01: The Docker simple quickstart pulled the pinned `0.5.2` image by its exact retained
  digest, reached readiness, and completed a real template-free conversion of
  `quickstart-source.md`. The download was a valid 10,459-byte OpenXML DOCX named
  `quickstart-source.docx`, confirming the T57 behavior through the published image. The equivalent
  rootless Podman pull was attempted but the local VM had only 964 MiB free and exhausted
  `/var/tmp` while storing an image layer; no application container was created. Hosted CI had
  already passed both Docker and rootless-Podman final-image profiles for the exact release source.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or
progress changes.
