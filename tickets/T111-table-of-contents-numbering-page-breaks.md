---
ticket: T111
linear_id: G1L-636
linear_url: https://linear.app/g1lom/issue/G1L-636/t111-add-table-of-contents-section-numbering-and-page-breaks
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T111 - Add table of contents, section numbering and page breaks

## Objective

Offer the most requested corporate Word features: an automatic table of contents, numbered sections and explicit page breaks, as template-level defaults with optional job overrides.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- Template metadata and job options control `--toc`, `--toc-depth` and `--number-sections`; options are frozen in the durable job and its traceability manifest.
- Explicit page breaks use a documented Markdown syntax handled by a bundled, reviewed internal filter (not a user filter).
- DOCX contains a real updatable TOC field; the PDF path shows a populated table of contents (LibreOffice must update indexes before export).
- API, browser, CLI and OpenAPI expose the options; golden DOCX/PDF tests and final-image E2E in both profiles cover them.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Decision D4 recorded in specification section 3.7.
- LibreOffice does not always populate a Pandoc TOC field on PDF export; a verified index-update step is required before PDF claims.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-636.
