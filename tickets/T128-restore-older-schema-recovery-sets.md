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
- 2026-10-11: Owner decision — minimum supported recovery-set revision is 20260829_14 (0.5.0).
  Recorded in the specification's backup/restore contract. Review round 1 corrections: both
  restore orchestrations now read the recorded revision before any target write, the recovery
  guide documents the profile-specific errors and cleanup, and the final-image recovery CLI smoke
  restores populated `20260923_24` sets in both profiles to head with readiness, then checks the
  refusal and cleanup of incomplete or unknown schemas.
- 2026-10-11: Review round 2 corrections:
  - Distributed restore now inserts template current-version pointers after the template
    versions they reference, and the conversion jobs that name a template pair after those
    pointers, with the integrity triggers left enabled. This also fixes restores of current-head
    sets.
  - SQLite read-only URIs now encode the path, so recovery sets under directory names containing
    `#`, `?` or `%` restore.
  - The production backup is back to its `main` behavior. Earlier-revision distributed test sets
    come from a test-only historical producer built on the migration-defined table allowlist.
  - Unknown and below-floor revisions are refused through restore, both in real PostgreSQL/S3
    integration tests and in the final-image smoke, with the target database and bucket left
    untouched.
  - Known limitation, outside this ticket: a retained conversion job that names a superseded
    template version still cannot be restored in the distributed profile, because the
    conversion-template integrity trigger accepts only the current pair. This is unchanged from
    `main`.
- 2026-10-11: Review round 3 approved. Documented the pre-existing superseded-template-version
  restriction on distributed restore in the recovery and upgrade guides; it is tracked as T132
  (G1L-685).

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-681.
