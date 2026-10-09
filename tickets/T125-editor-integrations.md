---
ticket: T125
linear_id: G1L-657
linear_url: https://linear.app/g1lom/issue/G1L-657/t125-add-vs-code-and-obsidian-integrations
status: Backlog
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T125 - Add VS Code and Obsidian integrations

## Objective

Let writers convert the current document from their editor through a Markweave server.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- A VS Code extension and an Obsidian plugin that authenticate with T114 tokens, list templates, submit the current file with its local images and download the result.
- Secrets stored in the editor's secure storage; no credentials in settings files.
- Automated tests and published packaging instructions.

## Dependencies

- T114 - Add scoped revocable API tokens
- T104 - Accept Markdown with local images without a hand-built ZIP

## Blocking points

- Separate marketplace publisher accounts are owner actions.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-657.
