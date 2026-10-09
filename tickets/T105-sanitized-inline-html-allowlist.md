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

- Define the closed allowlist approved in D3 with no attributes, or with an explicit attribute allowlist.
- Translate allowed elements before Pandoc into safe Markdown/Pandoc constructs; every other raw HTML node is still rejected before Pandoc.
- Security tests cover attribute injection, nesting, entity and encoding bypasses, unclosed tags and code spans/blocks (literal HTML there stays literal).
- Golden DOCX/PDF/PPTX cases cover each allowed element.
- Update `docs/pandoc-docx.md`.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Requires decision D3 (section 5 raw HTML rule).

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-624.
