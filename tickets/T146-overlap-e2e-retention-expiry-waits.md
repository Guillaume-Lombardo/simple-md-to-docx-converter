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

- 2026-10-21: Review of PR #322 (P2): a verifier that first read an already expired job did not
  check when the transition happened, so a job expired 60 s late passed. Both verifiers now also
  bound the expiry transition (`updated_at`) to `expires_at + 30 s`, whatever the time of the first
  read. A reclaimed conversion cleanup after the recovery SIGKILLs rewrites `updated_at` no later
  than the 6 s worker lease plus the restart; the reverse verifier runs in a phase without restarts.
  New unit cases cover an already expired late transition (rejected) and a late read of a timely
  transition (accepted); the rejection cases fail without the fix.

- 2026-10-21: Hosted timing evidence, three comparable pairs. Each pair runs the baseline (PR #323,
  same `main`, without T146) and T146 (PR #322) in the same minutes. Durations are the "Run selected
  final-image suite without a shell" step only: archive download and load time are excluded (the
  baseline branch also changes the load step), and cancelled attempts are not counted. All 24 E2E
  jobs passed on their first attempt, so both profiles completed final-image qualification each
  time.

  | Shard | Pair 1: 39604676996 → 39604675291 | Pair 2: 39610249167 → 39610248496 | Pair 3: 39626961561 → 39626960438 | Mean saved |
  |---|---|---|---|---|
  | e2e-standalone-1 | 446 → 381 s (65 s) | 460 → 446 s (14 s) | 404 → 345 s (59 s) | 46 s |
  | e2e-distributed-1 | 488 → 420 s (68 s) | 558 → 435 s (123 s) | 521 → 492 s (29 s) | 73 s |
  | e2e-standalone-2 | 536 → 461 s (75 s) | 513 → 462 s (51 s) | 506 → 473 s (33 s) | 53 s |
  | e2e-distributed-2 | 527 → 448 s (79 s) | 540 → 452 s (88 s) | 478 → 468 s (10 s) | 59 s |

  Waits recorded by the verify steps:

  | Step | Pair 1 | Pair 2 | Pair 3 |
  |---|---|---|---|
  | `verify-expiration`, standalone (shard 1) | 9.1 s | 1.0 s | 16.3 s |
  | `verify-expiration`, distributed (shard 1) | 18.8 s | 18.3 s | 2.6 s |
  | reverse `expiry-verify` (shard 2, both profiles) | 1.0–1.2 s | 1.1 s | 1.1–1.2 s |

  Before this change, each of these steps slept for the full retention window. Shard 1 saved 59.7 s
  on average over the three pairs (about 55 s expected) and shard 2 saved 56 s (about 60 s
  expected). The criterion is not met on every pair: pair 3 saved 59 and 29 s on shard 1 and 33
  and 10 s on shard 2, and pair 2 saved 14 s on e2e-standalone-1. The verify waits show that the
  retention windows now overlap other journeys in every run (the reverse wait is at most 1.2 s). So
  the spread between pairs comes from runner-to-runner variation in the rest of the suite (the same
  baseline shard ranges from 404 to 460 s), not from the change. Whether the per-pair criterion
  stands or the three-pair mean is accepted is an owner decision.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-704.
