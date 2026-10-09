---
ticket: T118
linear_id: G1L-648
linear_url: https://linear.app/g1lom/issue/G1L-648/t118-add-pdfa-and-tagged-accessible-pdf-output
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T118 - Add PDF/A and tagged accessible PDF output

## Objective

Offer archival PDF/A and tagged, accessible PDF through LibreOffice export options.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- Job or template option selects PDF/A level (for example PDF/A-2b) and tagged PDF.
- Fonts are embedded; the traceability manifest records the selected profile.
- CI validates outputs with a pinned veraPDF (or equivalent) validator.
- Golden and final-image E2E coverage in both profiles.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Requires decision D4.
- A PDF/A validator (veraPDF requires Java) must be added to the reviewed test toolchain.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-648.
