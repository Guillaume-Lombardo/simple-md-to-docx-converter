---
ticket: T122
linear_id: G1L-653
linear_url: https://linear.app/g1lom/issue/G1L-653/t122-add-html-epub-and-odt-outputs
status: Backlog
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T122 - Add self-contained HTML and ODT outputs

## Objective

Expose additional Pandoc output families for publishing and open formats.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- Self-contained HTML with no remote resources and a strict sanitizer policy, and ODT outputs, selectable in API, browser and CLI.
- Mermaid and images embedded; templates applied where meaningful.
- Golden and final-image E2E coverage; capability matrix updated.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Decision D11 recorded in specification section 3.7: HTML and ODT approved, EPUB deferred.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.
- 2026-10-09: Re-scoped by T96 decision D11: EPUB removed, HTML and ODT kept.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-653.
