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

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or
progress changes.
