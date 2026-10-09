---
ticket: T113
linear_id: G1L-637
linear_url: https://linear.app/g1lom/issue/G1L-637/t113-add-citations-and-bibliography
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T113 - Add citations and bibliography

## Objective

Support citations rendered by Pandoc citeproc from a BibTeX or CSL-JSON bibliography with administrator-managed CSL styles.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- Archive inputs may include a bibliography file; front matter selects it and a CSL style from an administrator-managed, versioned catalogue.
- Bibliography parsing is bounded and validated; no remote CSL or bibliography loading.
- Missing citation keys produce warnings or stable errors as decided in D6.
- Golden DOCX/PDF tests and final-image E2E in both profiles.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Requires decision D6, including CSL style licensing (many styles are CC BY-SA).
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-637.
