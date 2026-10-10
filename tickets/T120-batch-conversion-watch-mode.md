---
ticket: T120
linear_id: G1L-650
linear_url: https://linear.app/g1lom/issue/G1L-650/t120-add-batch-conversion-and-a-cli-watch-mode
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T120 - Add batch conversion and a CLI watch mode

## Objective

Convert many documents at once and regenerate outputs automatically while writing.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- CLI batch conversion of a directory or glob, through local mode and through the HTTP API, with bounded concurrency and a summary report.
- `--watch` regenerates outputs when the Markdown or referenced assets change (local mode).
- Server-side quotas and queue limits continue to apply to API batches.
- Unit, integration and E2E coverage.

## Dependencies

- T106 - Add local serverless CLI conversion

## Blocking points

- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-650.
