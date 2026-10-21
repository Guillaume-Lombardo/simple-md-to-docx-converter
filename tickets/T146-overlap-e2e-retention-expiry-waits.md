---
ticket: T146
linear_id: G1L-704
linear_url: https://linear.app/g1lom/issue/G1L-704/t146-overlap-e2e-retention-expiry-waits-with-other-journeys
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T146 - Overlap E2E retention-expiry waits with other journeys

## Objective

Remove the two fixed retention waits that sit on the final-image E2E critical path, without
weakening what they prove. This is part of K5 from the T139 audit (E2E time sinks), approved by
the owner on 2026-10-21.

Measured on scheduled main run 39109797384 (2026-10-18):

- **Shard 1 (`service` phase).** `tests/e2e/service_workflow.py::exercise` ends with a fixed
  `time.sleep(61)`. It waits for `MARKWEAVE_JOB_RESULT_RETENTION_SECONDS=60` before it proves that
  a durable conversion expires in place and that its result is denied. That is about 61 of the
  75 s that `service E2E exercise` takes. e2e-distributed-1 (13.0 min) is the slowest job and is
  on the CI critical path.
- **Shard 2 (`browser-workspaces` phase).** `tests/e2e/reverse_cli_workflow.py --phase expiry`
  submits a reverse job, waits for it to succeed, then polls for up to 90 s until
  `MARKWEAVE_REVERSION_RESULT_RETENTION_SECONDS=60` expires it. It took 67 s.

## Scope

- Split each expiry check into a submit step and a verify step: submit early in its phase, run the
  unrelated journeys of that phase, then verify.
- The verify step polls with a bounded deadline instead of a fixed sleep. It keeps every current
  assertion: the same job ID and correlation ID, the `expired` state, result or download denial,
  the error code, and no destination created.
- Keep the real retention values and the real cleanup path. Do not shorten retention and do not
  add a dedicated container.
- Keep each scenario in exactly one shard. `scripts/ci/e2e_shards.py` must still pass.

## Acceptance criteria

- Both expiry checks keep their assertions and still fail when the job does not expire within the
  bound, or when a result stays downloadable.
- Each verify step records how long it actually waited.
- e2e-standalone-1 and e2e-distributed-1 are about 55 s shorter, and both shard-2 jobs about 60 s
  shorter. This is measured on three consecutive full hosted runs, with before and after durations
  recorded.
- The canonical checks pass, and both E2E profiles pass on the final rootless images.

## Dependencies

- T135 (E2E shard layout). Done.

## Progress

- 2026-10-21: Created in Linear (G1L-704) from the owner-approved K5 recommendations and started on
  branch `perf/T146-overlap-e2e-expiry-waits` from `origin/main` `95bc188`.
- 2026-10-21: Implemented.
  - **Service expiry.** `exercise` no longer sleeps. After the expiring conversion succeeds, it
    writes a content-free state file (job ID, correlation ID, `expires_at`). The new
    `verify-expiration` command runs after `verify-recovery`, about 85 s later in the hosted log,
    so it normally finds the job already expired. It polls every 0.5 s until the job is `expired`,
    and fails if the job leaves `succeeded` for another state or is still unexpired 30 s after its
    `expires_at`. It then requires the same ID and correlation ID, an unchanged `expires_at`, an
    expiry transition (`updated_at`) no earlier than `expires_at`, and a 409 on the result.
  - **Reverse expiry.** `--phase expiry` becomes `expiry-submit`, which runs right after
    `use_browser_runtime` at the start of `browser-workspaces`, and `expiry-verify`, which runs
    after the browser journeys and before `unavailable`. The verify step logs in again, applies the
    same bounds and timestamp checks, and keeps the human and JSON download denials, the
    `reversion_conflict` code and the absent destination.
  - **Bound change.** The old service check required expiry within 61 s of the observed success.
    The verify steps now allow up to 30 s after `expires_at`. They add a check the old ones lacked:
    the job must not expire before its deadline. A strict 1 s bound is not kept, because a cleanup
    interrupted by the recovery SIGKILLs is reclaimed later, and the reclaim rewrites `updated_at`.
  - Both verify steps print the time they waited.
- 2026-10-21: Local verification. `ruff`, `ty` and the E2E unit tests (307) pass; the new unit tests
  cover polling to expiry, a late expiry, an early expiry, a substituted identity, a changed
  deadline, a non-succeeded state, the state-file schemas and the required arguments. The shard map
  places `service_workflow verify-expiration` in shard 1 and both reverse expiry steps in shard 2,
  and the CI validator passes. Not run locally: the final-image E2E (this container has no Podman).
  The hosted durations remain to be measured.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-704.
