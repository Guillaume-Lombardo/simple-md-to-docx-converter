---
ticket: T152
linear_id: G1L-711
linear_url: https://linear.app/g1lom/issue/G1L-711/t152-gate-the-reverse-expiry-submission-on-the-worker-broker-path
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T152 - Gate the reverse expiry submission on the worker broker path

## Objective

Make the T146 reverse expiry submission wait for a proven worker-to-broker path after the
browser runtime switch. The first reverse job of `browser-workspaces` should no longer race the
T142 post-restart broker stall.

## Evidence

PR #326 run 39643689513, `e2e-distributed-2` job 119378076059 (artifact 12366952539):

- `use_browser_runtime` restarted the application and started both distributed workers, but
  proves only API readiness.
- `expiry-submit` posted the reversion at 17:39:45. GET returned 200 without a terminal state
  until 17:40:15, and the CLI exited at 17:40:16 after its 30-second wait.
- Worker 1 became `degraded_broker` at 17:39:40, and every reconciliation then ended after 5 s
  with `transport_failure`. The workers and API stayed running, and the containers were not
  OOM-killed.
- This matches the documented T142 post-runtime-switch container-to-host broker stall. Since
  T146, `expiry-submit` is the first reverse operation after the browser switch.

## Scope

- Add `wait_for_reverse_workers_broker` to `scripts/e2e/run.sh`. It runs the success-only
  `reverse_broker wait-worker-broker` gate from T142 against every reverse worker: the
  application in standalone, both workers in distributed.
- Run it in `e2e_phase_browser_workspaces` after `use_browser_runtime` and before
  `expiry-submit`.
- Do not raise the CLI timeout and do not weaken any expiry or retention assertion.

## Acceptance criteria

- A harness unit test proves the ordering and the per-worker gate, and fails without the change.
- The canonical checks pass.
- Both E2E profiles pass on the final rootless images.

## Dependencies

- T142 (`wait-worker-broker` gate). Done.
- T146 (expiry submit/verify split). Done.

## Progress

- 2026-10-21: Created from the owner's review of PR #326 and started on branch
  `fix/T152-gate-expiry-on-worker-broker` from `origin/main` `a143a09`.
- 2026-10-21: Implemented as scoped.
  - The new harness test checks three things: the gate runs after `use_browser_runtime` and
    before `--phase expiry-submit`, it runs once per worker metrics endpoint, and the endpoints
    are the published 9464 port of each distributed worker or the application's 8080 port in
    standalone. It fails without the change.
  - `ruff`, `ty`, the CI validator and the E2E unit tests (312) pass.
  - Not run locally: the final-image E2E, because this container has no Podman.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-711.
