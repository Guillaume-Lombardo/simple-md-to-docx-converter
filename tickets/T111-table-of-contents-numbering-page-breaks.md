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
- Spike 2026-10-09 (Pandoc 3.1.3, LibreOffice 24.2): `soffice --convert-to pdf` leaves the table of contents empty; updating the document indexes through a fixed LibreOffice UNO script before PDF export populates entries and page numbers. Use that UNO path only when the document contains an index, keep the existing `--convert-to` path otherwise, and include a security review of the UNO path (isolated profile, local pipe, process-group termination). Confirm with the pinned versions.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-636.
