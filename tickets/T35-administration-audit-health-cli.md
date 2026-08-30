---
ticket: T35
linear_id: G1L-411
linear_url: https://linear.app/g1lom/issue/G1L-411/t35-add-administration-audit-and-health-cli-commands
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T35 - Add administration, audit, and health CLI commands

## Objective

Expose user administration, audit inspection, service health, readiness, and metrics retrieval through HTTP-only CLI commands.

## Acceptance criteria

* Implement user list/create/activate/deactivate/reset/force-password-change commands that preserve existing authorization and revocation rules.
* Implement paginated audit queries and health/live, health/ready, and metrics inspection with deterministic human and JSON output.
* Require explicit confirmation or non-interactive force flags for sensitive mutations and never print generated or submitted passwords unless an explicitly documented secure one-time output contract is approved.
* Use only HTTP endpoints and authenticated profiles except for public health endpoints where the server permits them.
* Cover restricted sessions, two users and one administrator, pagination, readiness failures, sanitized errors, and final-image behavior.

## Dependencies

* T32
* T17
* T19
* T30

## Implementation boundary

* Own only T31's pre-registered administration/audit/health family modules and domain tests/documentation; do not edit the root registry, shared help snapshots, documentation index, or other command families.
* Do not change authorization or audit semantics except for a separately documented API contract defect.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up restricted this worker to T31's administration/audit/health family and excluded shared registry, help, and documentation-index files.
* 2026-08-30: Implementation started on `feat/T35-admin-audit-health-cli` from `c1cae3b6ca1d2f8eb6e680eec26f444ea92332c5`. The work remains limited to T31's pre-registered administration/audit/health family, its domain tests, and dedicated documentation.
* 2026-08-30: Implemented HTTP-only user administration, paginated audit retrieval, and public live/readiness/metrics commands with deterministic human/JSON output, confirmation and terminal-only password safeguards, bounded responses, and sanitized errors. Added unit tests, real-loopback HTTP integration tests covering two users, one administrator, restricted sessions, revocation, pagination, and readiness failure, plus a dedicated final-image workflow driver and documentation. Ruff formatting/linting and `ty check` pass; 32 focused tests pass, and the changed administration module reaches 95% coverage. Final-image execution and reconciliation of the two superseded T31 placeholder cases remain pending the mandated T34 -> T33 -> T35 integration sequence, so the ticket remains In Progress.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
