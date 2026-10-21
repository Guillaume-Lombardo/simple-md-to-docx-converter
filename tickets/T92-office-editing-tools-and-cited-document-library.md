---
ticket: T92
linear_id: G1L-591
linear_url: https://linear.app/g1lom/issue/G1L-591/t92-deliver-bounded-office-editing-tools-and-cited-document-library
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T92 - Deliver bounded Office editing tools and cited document library

## Objective

Deliver the T88-qualified structured Office operations and a permission-aware cited reference
library after the author directory is available.

## Acceptance criteria

- Read and modify real DOCX text, tables, sections, images, and styles and real PPTX slides, text,
  tables, images, styles, objects, and notes through bounded schemas and stable targets. FastAPI
  validates and executes on copies without arbitrary shell/code; only qualified operations are
  exposed and unsupported operations fail explicitly.
- Preserve non-target OOXML, formatting, relationships, and media, and verify real-file results,
  controlled revisions, exact downloads, diffs, and restore. Reject unsafe archives/XML, embedded
  active content, external loading, and invalid targets without publication.
- Add permissioned library search and source-version citations. Filter access before retrieval or
  transmission, revalidate after revocation and on publication, and leave user review explicit.
  Scan every new library upload before indexing or persistence; an authorized immutable conversion
  handoff may reuse verified scan provenance, while changed or unattested bytes require a new scan.
  Test infected inputs and unavailable scanners fail closed.
- Test prompt injection/tool misuse, invalid responses, preservation, concurrency, recovery,
  permissions, and long files with unit, real-boundary integration, and final-rootless-image E2E
  proof in both profiles.
- Complete independent review, required checks, and qualified docker-box candidate deployment.

## Dependencies

T91.

## Progress

- 2026-09-22: Created in Linear and mirrored before implementation. No delivery is claimed.
- 2026-10-21: Started from `main` `95bc188` after T89–T91 reached Done; G1L-591 moved to In
  Progress. First increment: the DOCX body-text edit family only.
  `markweave.composer.docx_edits` admits a private copy under the existing bounded Word
  archive/OPC checks (active content, signatures, and every external relationship except
  never-fetched hyperlinks are rejected), lists stable `word/document.xml#t<n>` text targets with
  paragraph and table context, and replaces whole `w:t` nodes under an expected-text
  precondition by splicing exact bytes. Every other package member and byte is preserved and
  re-verified after writing; tracked changes, content controls, field results, alternate
  content, self-closing, attributed, and control-character nodes are never targets.
  `GET .../revisions/{revision_id}/docx-text-targets` and
  `POST .../revisions/{revision_id}/docx-edits` (current revision only, `If-Match`,
  `Idempotency-Key`) publish an immutable revision with matching DOCX download/preview, frozen
  operation record, `human:docx_edit` provenance, and lineage; failures return
  `COMPOSER_DOCX_EDIT_*` with target identities only. OpenAPI, the T41 fixtures, and the web
  bindings are regenerated; the API and user guides document the operation and its limits.
  Unit (100% branch coverage of the executor), SQLite HTTP integration, real-corpus
  preservation, and Pandoc-generated DOCX tests pass locally. The LibreOffice rendering test
  could not pass on this host: its LibreOffice 24.2.7.2 differs from the pinned 26.2.5.2
  toolchain and the existing T91 rendering test fails identically on `main`. Open in later
  increments: table, section, image, and style structure edits; every PPTX family; model
  tool-call proposals using the same executor; semantic DOCX diff; CLI and browser controls;
  the permissioned cited library with scanned uploads; PostgreSQL/S3 and final-rootless-image
  E2E proof in both profiles; independent review and docker-box deployment. No acceptance
  criterion is claimed complete.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-591.
