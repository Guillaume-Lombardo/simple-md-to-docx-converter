---
ticket: T95
linear_id: G1L-594
linear_url: https://linear.app/g1lom/issue/G1L-594/t95-correct-retention-aware-restore-object-verification
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T95 - Correct retention-aware restore object verification

## Objective

Correct backup/restore stable-object verification so intentionally retention-cleaned expired
uploads do not block an otherwise valid isolated restore, while every missing object still
required by retained data fails closed.

## Confirmed trigger

The authorized T91 docker-box deployment stopped before migration during fresh isolated restore
verification. Fourteen uploads were absent in both the live T90 store and its backup; independent
grouping confirmed all were expired, cleanup-complete, and past retention. No active, unexpired,
or incomplete upload was missing, and no live data loss was observed. The verifier still treats
stale `source_ready` references for those cleaned rows as required objects. The exact prior T90
service was restored and verified. T91 deployment remains blocked.

## Acceptance criteria

- Define retained stable-object references consistently with the existing retention contract: a
  missing upload may be ignored only when authoritative `state=expired` and
  `cleanup_completed=true` both hold. Do not add a restore-time wall-clock expiration check.
  Preserve validation of every retained/current object and fail closed on ambiguous metadata.
- Continue rejecting missing active, non-expired, or cleanup-incomplete upload objects,
  corrupt/mismatched backup manifests, and any other required stable object. Do not broadly
  disable verification, alter live data, or bypass backup integrity.
- Cover the real retention-cleanup to backup to isolated-restore successful path and relevant
  missing-object failure paths in standalone SQLite/files and distributed PostgreSQL/S3 profiles.
  Add focused unit tests and final rootless-image recovery E2E proof for both profiles.
- Preserve content-free recovery reports, isolated empty-target restore,
  pre-migration/readiness verification, and unchanged production data and retention policy.
  Update only directly relevant recovery documentation if behavior needs clarification.
- Run applicable canonical formatting, linting, type checking, Python, coverage, and both-profile
  final-image checks; obtain independent review. T91 candidate deployment resumes only after the
  correction and a newly qualified isolated restore, under separate deployment authorization.

## Dependencies and impact

- T37 backup/restore and T18 retention are completed implementation baselines.
- This fix blocks T91 docker-box deployment acceptance; T91 remains In Progress until its
  separately qualified deployment.
- No live data repair, deployment, or release publication is authorized by this ticket.

## Progress

- 2026-09-26: Owner authorized the scoped recovery-verifier correction after confirmed read-only
  diagnosis. Implementation starts on `fix/T95-retention-aware-restore-verification` from clean
  merged main `6a98d981c1628f213a3481eac041b2c11a1535d5`. No source correction or new
  restore qualification is claimed yet.
- 2026-09-26: The scoped verifier correction applies the authoritative `state=expired` plus
  strict `cleanup_completed=true`/integer `1` rule without a wall-clock check. Focused recovery
  validation passed 46 unit/integration tests, including actual standalone SQLite/files and
  distributed PostgreSQL/S3 cleanup-to-backup-to-isolated-restore paths, missing active or
  incomplete upload refusal, and malformed cleanup flags. Ruff format/lint, `ty`, and diff checks
  pass; independent code/test review found no blocking defects. The final-image recovery fixture
  now seeds real completed cleanup in both profiles. Full canonical Python and complete final-image
  profile runs remain pending; no T95 completion or T91 deployment qualification is claimed.
  An operational rollback caveat remains: the new CLI auto-migrates to schema 26, whereas the live
  T90 rollback baseline is schema 24. A compatible old recovery-tool patch is prepared in the
  local deployment plan but has not been applied or used for live deployment.
- 2026-09-27: Clean source `2a38d7000e120cf25faa7194d05cbebd881be27b` passed the
  canonical engine-excluding Python suite: 5,376 passed, 60 engine-marked deselected, 19
  warnings; total coverage 94.17%, application branch coverage 90.16% (8,336/9,246), and
  changed Python lines 100% (3/3). Ruff format/lint, `ty`, shell syntax, and diff checks pass.
  The first partial Python run lacked the pinned local reverse base image and is not counted;
  after the repository's pinned-image bootstrap, the fresh full run passed. The canonical
  backend image built successfully. Dedicated final-image recovery smoke passed in standalone
  and distributed profiles, covering genuine retention-cleaned backup/isolated restore plus
  manifest-valid missing active and cleanup-incomplete upload rejection before publication.
  Both complete unattended final-image E2E profiles then passed with scenario-only flags unset.
  Local receipt and checksummed logs are in `/home/g1lom/dev/scratch/t95-validation/`.
  The host full-engine Python suite was unavailable; image E2E exercised bundled engines.
- 2026-09-27: A separately reviewed local, network-none T90 schema-24 recovery child image
  preserved the exact old image layers and changed only the recovery module. Synthetic old-image
  proof reproduced the expired-cleaned rejection; the child passed real cleanup, backup, and
  isolated restore at schema 24 while still rejecting missing active/incomplete uploads. This
  qualifies only the disposable local rollback tool. No fresh live host backup/restore has passed,
  no infrastructure changed, and T91 deployment remains blocked pending a separately authorized
  and qualified host restore. T95 is unpublished and unverified on main, so it stays In Progress.
  The unrelated T94 post-merge main CI scanner-probe failure is recorded separately; current
  local T95 standalone and distributed final-image profiles both passed and do not resolve it.
- 2026-09-27: The first PR #272 hosted run at `140c405` ended with 14 of 16 jobs passing;
  only `CI / light` and its derived gate failed on an unchanged typed-template pagination test.
  Both complete hosted E2E profiles and other domains passed. The owner authorized a scoped
  test-only correction, committed as `989a928bcfd66d00cd43f328c1d33203d2697eeb`: unique
  template IDs across fixture pages and a ten-second limit for only the heavy pagination case,
  preserving its assertions. Independent review approved it with no duplicate-ID warning.
  Targeted web tests pass 47/47; full web checks pass 699/699 with 90.02% branch coverage,
  formatting, lint, types, bindings, and structure checks. The original T95 Python, recovery
  smoke, and both-profile image qualification remain valid because this commit changes only the
  web test. A new exact-head hosted run, protected merge, and main verification remain pending;
  T95 stays In Progress and T91 deployment remains blocked.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-594.
