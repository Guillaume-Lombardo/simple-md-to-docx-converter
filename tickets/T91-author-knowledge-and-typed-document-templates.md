---
ticket: T91
linear_id: G1L-590
linear_url: https://linear.app/g1lom/issue/G1L-590/t91-add-structured-author-knowledge-and-typed-document-templates
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T91 - Add structured author knowledge and typed document templates

## Objective

Add permission-aware structured author knowledge and genuine typed document filling while
retaining the current Pandoc style-reference templates.

## Acceptance criteria

- Provide a personal or explicitly shared structured author directory, selection among authorized
  entries, source provenance, audited share/revoke, and permission checks before model transmission
  and on resumed work.
- Add typed fields, constraints, and bounded repeatable sections for filling templates; distinguish
  them from current Pandoc style references, retain existing template compatibility and visibility,
  and reject malformed schemas or invalid values without publication.
- Ask for missing author, finding, decision, and other required data and resolve ambiguities through
  reviewable proposals; later model output must not silently overwrite manual edits.
- Freeze approved values and exact template versions for regeneration without an LLM call.
- Expose documented API, CLI, and UI operations, audit/configuration controls, and primary/failure
  integration and final-rootless-image E2E tests in both profiles, including sharing/revocation,
  malformed schemas, concurrency, and reproducibility.
- Complete independent review, required checks, and qualified docker-box candidate deployment.

## Dependencies

T90.

## Progress

- 2026-09-22: Created in Linear and mirrored before implementation. No delivery is claimed.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-590.
