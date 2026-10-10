---
ticket: T106
linear_id: G1L-626
linear_url: https://linear.app/g1lom/issue/G1L-626/t106-add-local-serverless-cli-conversion
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T106 - Add local serverless CLI conversion

## Objective

Provide `markweave convert` for local files, without a server, an account or ClamAV, reusing the same validation and engine pipeline.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to change, item 2).

## Acceptance criteria

- A local command (for example `markweave convert doc.md --to docx,pdf --template t.docx -o out/`) runs the existing validation, archive, image, Mermaid, Pandoc and LibreOffice adapters with the same limits.
- It records the engine versions it used, fails clearly when a required engine is missing, and warns when a version differs from the approved pin; `--require-pinned` turns that warning into a failure (D1).
- A documented container invocation runs the command with the bundled engines.
- Business HTTP commands are unchanged.
- Unit, integration (real engines, marked) and final-image E2E tests cover success, missing engine, invalid input, timeout and cancellation.
- Update `docs/cli.md`, `docs/conversion-cli.md` and README.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Decision D1 recorded in specification section 3.7.
- Local mode depends on host engines; reproducibility guarantees must be stated honestly when host versions differ from the pinned toolchain.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-626.
