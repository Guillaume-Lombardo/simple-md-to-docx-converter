---
ticket: T121
linear_id: G1L-652
linear_url: https://linear.app/g1lom/issue/G1L-652/t121-provide-a-github-action-and-a-pre-commit-hook
status: Backlog
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T121 - Provide a GitHub Action and a pre-commit hook

## Objective

Let repositories build DOCX/PDF/PPTX from Markdown in CI and validate Markdown before commit.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- A GitHub Action that runs the published image in local conversion mode and uploads outputs as artifacts; inputs pinned by digest.
- A pre-commit hook that validates Markdown against the approved dialect without converting.
- Example workflow, documentation and an integration test in CI.

## Dependencies

- T106 - Add local serverless CLI conversion

## Blocking points

- Requires a published image that includes T106.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-652.
