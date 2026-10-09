---
ticket: T108
linear_id: G1L-632
linear_url: https://linear.app/g1lom/issue/G1L-632/t108-freeze-the-experimental-reverse-conversion-scope
status: Backlog
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T108 - Freeze the experimental reverse conversion scope

## Objective

Keep the experimental 2md reverse conversion, but freeze its feature scope until the owner revisits it.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to remove or freeze (owner kept 2md)).

## Acceptance criteria

- Record in README/`docs/index.md` the D15 freeze already stated in specification section 3.7: 2md is kept, experimental and feature-frozen: only security fixes, dependency updates and regressions are accepted.
- List the reverse-conversion tickets and ideas that are paused (including OCR and the T74 Kubernetes backend).
- No code is removed; all reverse-conversion tests remain in CI.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Owner decides when the freeze ends; that decision reopens scope through a new ticket.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-632.
