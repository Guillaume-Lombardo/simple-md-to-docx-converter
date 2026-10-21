---
ticket: T148
linear_id: G1L-706
linear_url: https://linear.app/g1lom/issue/G1L-706/t148-spike-shorten-reverse-worker-leases-in-the-e2e-runtime
status: Backlog
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T148 - Spike: shorten reverse worker leases in the E2E runtime

## Objective

Find out whether shorter reverse worker leases in the E2E runtime can shorten the reverse recovery
scenarios without weakening them or causing flakes. This is part of K5 from the T139 audit,
approved by the owner on 2026-10-21 as a measured spike.

Measured on scheduled main run 39109797384 (2026-10-18), shard 2 (`reverse-lifecycle` phase):

- `verify` after the worker-restart fault took 39.9 s;
- `verify` after the broker-restart fault took 39.5 s;
- `verify-member` took 34.6 s.

Recovery requires a new attempt (`attempt >= 2`), so these steps wait for the lease to expire.
`scripts/e2e/runtime-settings.sh` sets `MARKWEAVE_REVERSION_WORKER_LEASE_SECONDS=30` and
`MARKWEAVE_REVERSION_WORKER_RECOVERY_LEASE_SECONDS=30`, with a 1 s heartbeat. The only
configuration constraint is that the heartbeat stays shorter than the lease.

## Scope

- Evaluate E2E-only lease values of about 8 to 10 s against the broker dispatch, termination and
  cgroup-emptiness budgets that T137 introduced, and against
  `MARKWEAVE_REVERSION_WORKER_MAX_DURATION_SECONDS=30`.
- Run the reverse lifecycle phases repeatedly with the candidate values, on hosted runners under
  load. Report the saving and any premature recovery or flake.
- Change nothing in the product defaults.

## Acceptance criteria

- A recommendation with evidence: adopt the values, with the measured saving over at least five
  hosted runs and no premature recovery; or reject them, with the observed failure mode.
- If adopted, a follow-up change keeps every recovery assertion, and both profiles pass three
  consecutive full hosted runs.

## Dependencies

- T137 (bounded broker termination). Done.

## Progress

- 2026-10-21: Created in Linear (G1L-706) from the owner-approved K5 recommendations. Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-706.
