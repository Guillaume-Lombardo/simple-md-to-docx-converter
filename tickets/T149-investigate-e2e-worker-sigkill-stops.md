---
ticket: T149
linear_id: G1L-707
linear_url: https://linear.app/g1lom/issue/G1L-707/t149-investigate-e2e-worker-containers-that-need-sigkill-to-stop
status: Backlog
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T149 - Investigate E2E worker containers that need SIGKILL to stop

## Objective

Find out why a distributed worker container did not stop on SIGTERM within 15 s during final-image
E2E. Either fix the shutdown or document why it is expected. This is part of K5 from the T139
audit, approved by the owner on 2026-10-21.

Observed on scheduled main run 39109797384 (2026-10-18), e2e-distributed-1, `service` phase:
`StopSignal SIGTERM failed to stop container md-converter-t21-distributed-worker-2 in 15 seconds,
resorting to SIGKILL`. The harness stops the workers with `podman stop --time 15` in several places
in `scripts/e2e/run.sh` (Composer snapshot restore, key-loss continuity, restart scenarios). Each
such occurrence costs 15 s. A worker that ignores SIGTERM is also an operational concern, because
OpenShift and Compose use a termination grace period.

## Scope

- Reproduce, and determine what the worker is doing when SIGTERM arrives: an idle poll, a leased
  job, a heartbeat, cleanup, or a blocked engine subprocess.
- Decide between a product fix (graceful shutdown within a documented bound) and a harness change,
  for steps where an abrupt stop is the intended scenario.
- Do not shorten the stop timeout to hide a shutdown defect.

## Acceptance criteria

- The root cause is documented with evidence.
- If it is a product defect: a fix with an integration test showing that a worker with in-flight
  work exits within the documented bound on SIGTERM, and the E2E stops no longer reach SIGKILL in
  three consecutive full hosted runs.
- If it is expected: the reason is documented, and the harness uses an explicit kill where an
  abrupt stop is the scenario.

## Dependencies

- None.

## Progress

- 2026-10-21: Created in Linear (G1L-707) from the owner-approved K5 recommendations. Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-707.
