---
ticket: T105
linear_id: G1L-624
linear_url: https://linear.app/g1lom/issue/G1L-624/t105-allow-a-sanitized-inline-html-subset
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T105 - Allow a sanitized inline HTML subset

## Objective

Stop rejecting common harmless inline HTML found in real Markdown, such as `<br>` inside table cells, by translating a closed allowlist to Pandoc constructs.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to change, item 3).

## Acceptance criteria

- Implement exactly the D3 allowlist (`<br>`, `<sub>`, `<sup>`, `<kbd>`, `<u>`, no attributes) and remove HTML comments.
- Translate allowed elements before Pandoc into safe Markdown/Pandoc constructs; every other raw HTML node is still rejected before Pandoc.
- Security tests cover attribute injection, nesting, entity and encoding bypasses, unclosed tags and code spans/blocks (literal HTML there stays literal).
- Golden DOCX/PDF/PPTX cases cover each allowed element.
- Update `docs/pandoc-docx.md`.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Decision D3 recorded in specification section 3.7.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-624.
