---
ticket: T123
linear_id: G1L-654
linear_url: https://linear.app/g1lom/issue/G1L-654/t123-add-deterministic-data-driven-docx-template-filling
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T123 - Add deterministic data-driven DOCX template filling

## Objective

Fill typed DOCX templates from structured JSON data (fields, repeated sections, conditions, tables) through the API without an LLM, like docxtemplater or Carbone.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- Reuse the T91 typed-template model and validation; no second template engine.
- API and CLI accept JSON validated against the template schema and produce DOCX/PDF through the durable queue.
- Bounded loops and conditions; no code execution; security and golden tests; final-image E2E in both profiles.

## Dependencies

- T91 - Add structured author knowledge and typed document templates

## Blocking points

- Depends on T91 typed templates being Done on main; scope must not overlap T92.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-654.
