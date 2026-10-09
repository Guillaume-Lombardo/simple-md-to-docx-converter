---
ticket: T124
linear_id: G1L-656
linear_url: https://linear.app/g1lom/issue/G1L-656/t124-add-an-administrator-approved-pandoc-lua-filter-catalogue
status: Canceled
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T124 - Add an administrator-approved Pandoc Lua filter catalogue

## Objective

Allow administrators to install reviewed Lua filters that users can select, without letting users upload filters.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- Filters are immutable, versioned, administrator-owned artifacts with audit, like templates.
- Filters run in a restricted Lua environment without `io`, `os`, `pandoc.system` process or network access, under existing deadlines and workspace isolation.
- Security tests attempt file, process and network escape; golden tests cover a sample filter.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification
- T111 - Add table of contents, section numbering and page breaks

## Blocking points

- Rejected by decision D12 (specification section 3.7): Pandoc's `--sandbox` does not restrict Lua filters, so an administrator-supplied filter would be arbitrary code on the worker. Cancelled.
- Reuses the internal-filter mechanism introduced by T111.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.
- 2026-10-09: Cancelled by T96 decision D12.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-656.
