---
ticket: T114
linear_id: G1L-639
linear_url: https://linear.app/g1lom/issue/G1L-639/t114-add-scoped-revocable-api-tokens
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T114 - Add scoped revocable API tokens

## Objective

Allow scripts, CI and integrations to call the HTTP API with scoped, revocable, expiring tokens instead of an interactive session.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- Users create, list and revoke their own tokens; administrators can revoke any token. Secrets are shown once and stored only as hashes.
- Scopes cover at least conversion, reverse conversion, templates read/write and administration; every use is audited.
- Token-authenticated requests are exempt from CSRF only as defined by D7; session behavior is unchanged.
- CLI profiles support token authentication without storing secrets in process arguments.
- Security tests (scope escalation, revoked/expired tokens, timing-safe comparison), both storage profiles, OpenAPI and final-image E2E.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Requires decision D7 (section 2 'no API token').
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-639.
