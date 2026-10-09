---
ticket: T110
linear_id: G1L-634
linear_url: https://linear.app/g1lom/issue/G1L-634/t110-remove-legacy-md-converter-configuration-aliases-at-10
status: Backlog
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T110 - Remove legacy MD_CONVERTER_* configuration aliases at 1.0

## Objective

Remove the legacy `MD_CONVERTER_*` aliases as already promised for 1.0 by the specification.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to remove or freeze).

## Acceptance criteria

- Remove alias resolution from `src/markweave/config.py` and its tests.
- Add a startup error that names the canonical `MARKWEAVE_*` variable when a legacy variable is present.
- Document the break in CHANGELOG and the upgrade guide.

## Dependencies

- None.

## Blocking points

- Blocked until the owner schedules the 1.0 release; aliases must remain through 0.x.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-634.
