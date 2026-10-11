---
ticket: T128
linear_id: G1L-681
linear_url: https://linear.app/g1lom/issue/G1L-681/t128-restore-recovery-sets-created-by-an-older-schema
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T128 - Restore recovery sets created by an older schema

## Objective

Let `markweave restore` accept a verified recovery set created by an earlier supported schema and
migrate it, instead of refusing it because the pre-migration database lacks tables added by later
migrations.

Found on 2026-10-11 while preparing the T89/T90/T91 docker-box candidate deployment: a schema
`20260923_24` recovery set would be refused by the current image with "Restored Composer schema is
incomplete". `StandaloneRecoveryAdapter.restore` runs `_verify_stable_references_sqlite` (which
requires every current `composer_*` table, including `composer_fill_plans` added by migration
`20260924_25`) before `upgrade_database`. The distributed restore also verifies payload references
before migrating.

## Acceptance criteria

- Restore verifies an older-schema recovery set against the schema it was created with (or
  verifies after migration), then migrates it to the current head; integrity, isolation and
  stable-object-reference guarantees are unchanged.
- A genuinely incomplete or tampered schema is still refused with a stable error.
- Recovery sets from a newer, unknown schema are refused with a stable error.
- Unit tests plus standalone and distributed integration tests restore recovery sets created at an
  earlier supported migration (at least the pre-T91 schema `20260923_24`) and prove the restored
  instance reaches the current head with all references intact; regression tests cover the
  incomplete-schema refusal.
- Update the backup/restore and upgrade documentation to state which older recovery sets are
  supported.

## Dependencies

- None.

## Blocking points

- None known. The supported range of older recovery-set schemas must follow the existing upgrade
  contract in the specification; if the specification does not define it, request an owner
  decision rather than inventing one.

## Progress

- 2026-10-11: Created in Linear and mirrored here. Implementation started on
  `fix/T128-older-schema-restore`. Reproduced on main: a standalone set created at
  `20260923_24` is refused with "Restored database schema is incomplete" (the Composer-specific
  message was wrapped by the generic handler); the distributed backup and restore paths also
  assumed the current model schema. The fix verifies every set against the schema of its recorded
  revision (rebuilt from the repository migrations), migrates it to head, and verifies references
  again at head; the distributed profile recreates the recorded revision, inserts rows and
  migrates to head in one transaction. Unknown or newer revisions and revisions older than
  `20260829_14` (release 0.5.0, the first with `markweave backup`) are refused with stable errors.
  The specification does not define a minimum supported recovery-set revision; that floor is
  recorded as an owner decision point in the pull request. Release-tag checks confirmed that each
  released model schema from 0.5.0 to 0.7.3 equals the migration-defined schema at its revision.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-681.
