---
ticket: T112
linear_id: G1L-643
linear_url: https://linear.app/g1lom/issue/G1L-643/t112-add-cross-references-for-figures-tables-and-equations
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T112 - Add cross-references for figures, tables and equations

## Objective

Support numbered captions and references to figures, tables and equations, as Quarto and pandoc-crossref do.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- Documented syntax for labels and references; numbering is consistent in DOCX, PDF and PPTX where meaningful.
- Unresolved references produce a warning or a stable error, as decided in D5.
- Golden and E2E coverage; supply-chain record for any new binary.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification
- T111 - Add table of contents, section numbering and page breaks

## Blocking points

- Decision D5 recorded in specification section 3.7: a reviewed internal filter, no `pandoc-crossref` binary.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-643.
