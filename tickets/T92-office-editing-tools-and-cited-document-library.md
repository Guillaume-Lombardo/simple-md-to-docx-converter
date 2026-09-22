---
ticket: T92
linear_id: G1L-591
linear_url: https://linear.app/g1lom/issue/G1L-591/t92-deliver-bounded-office-editing-tools-and-cited-document-library
status: Backlog
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

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-591.
