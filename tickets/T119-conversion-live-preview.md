---
ticket: T119
linear_id: G1L-649
linear_url: https://linear.app/g1lom/issue/G1L-649/t119-add-a-live-preview-to-the-conversion-workspace
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T119 - Add a live preview to the conversion workspace

## Objective

Show users a preview of their document before or after conversion, reusing the private preview infrastructure qualified for Composer.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- The 2docx workspace previews the produced DOCX/PDF with the qualified isolated preview components and the existing CSP.
- Optionally, a fast Markdown preview before submission clearly labelled as approximate.
- Accessibility, long-document and failure behavior are tested; final-image E2E covers the primary path.

## Dependencies

- T90 - Deliver Composer chat, proposals and revision previews

## Blocking points

- Depends on the T90 preview components being Done on main.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-649.
