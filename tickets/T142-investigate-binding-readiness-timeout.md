---
ticket: T142
linear_id: G1L-695
linear_url: https://linear.app/g1lom/issue/G1L-695/t142-investigate-the-intermittent-t73-diagnostic-binding-readiness
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T142 - Investigate the intermittent T73 diagnostic binding readiness timeout in final-image E2E

## Problem

Hosted run 38334229508 failed in `CI / e2e-distributed-2`, browser-runtime phase. This was PR #302 (T138) after it was updated onto main 016bbe7, which contains T137.

`scripts/e2e/reverse_broker.py watch_pause` → `_await_binding` raised `RuntimeError("T73 diagnostic binding readiness timed out")`. The rerun of the failed job passed.

The `reverse-lifecycle` and `browser-workspaces` phases passed in the same job, and T137 did not change `_await_binding`. T137 did make every broker dispatch bounded by a budget of 90% of the transport operation timeout. That could change how quickly the broker inventory or attempt binding becomes observable under load. A plain race in the watcher is also possible.

## Scope

- Find out what the watcher was waiting for and why it did not appear in time. Use the failure artifacts of run 38334229508, the worker and broker logs, and the inventory state.
- Determine whether T137's dispatch budget is involved, for example create or stage refusals near the budget that the worker then retries.
- Fix the root cause, in the product or in the harness. Do not simply lengthen the timeout unless the evidence shows the bound itself is wrong.

## Acceptance criteria

- The root cause is documented with evidence.
- If the product is at fault, a regression test fails without the fix.
- The `e2e-distributed-2` and `e2e-standalone-2` shards pass in three consecutive hosted runs.

## Dependencies

- None. Scheduled after T141, which changes the same E2E broker harness.

## Progress

- 2026-10-13: Created from the hosted failure during the T138 merge.
- 2026-10-13: Root cause identified from run 38334229508 attempt 1, job 115092341947 (`e2e-distributed-2`, browser-runtime phase, head `b720cb3`, which has T137 but not T141).
  - Timeline: `restart_backend_and_router` restarted the API at 02:08:18.4–19.5. Worker-1's claim at 02:08:19.529 was its last normal step. Its broker exchanges then failed with `transport_failure` at 24.64, 29.89, 35.13, 42.24, 47.47, 52.70, 57.93, 03.17 and 08.39, each one ending the 5.006-second client deadline. The worker stayed `degraded_broker` from 02:08:24.64 until the exchange started at about 02:09:08.63 succeeded at 11.77, after 3.14 seconds (the 1 s + 2 s SYN retransmission signature). It was `ready` again at 02:09:12.10.
  - `run_reverse_lifecycle broker-restart true` started its watcher at 02:08:40.75, inside the stall. The worker could not reach the broker, so no recovery attempt bound before the 30-second `_await_binding` deadline at 02:09:11.12; the pause state recorded `prearmed` at 629 ms, `failed` at 30010 ms and no target. The broker log is empty: no gate, watchdog or fatal broker failure occurred.
  - The same stall follows container stop or restart operations in other hosted runs: 38255823107 (after the worker-1 restart, failures from 13:39:28 to 13:40:05; after the API restart at 13:46:43, failures from 13:46:49 to 13:47:52, about 70 seconds), 38062715491 (after the ClamAV stop, 15:44:29–45) and 38130110398 (10:20:58–10:21:30 and 10:22:39–55). Those runs passed because no bounded fault window overlapped the stall.
  - Ruled out: T137's dispatch budget (every runtime path is clamped to the remaining operation deadline and the broker was idle, so no dispatch overran); the T141 slot race (admission refusals are immediate resets, not 5-second timeouts, and the worker client is sequential); and a watcher race (the watcher was armed and waiting; the worker never reached the broker).
  - Root cause: an environmental container-to-host stall of rootless Podman 4.9.3 on the hosted runner after container lifecycle operations, combined with a harness defect. `run_reverse_lifecycle` started its bounded 30-second fault window without first proving that the worker could reach the broker. The 30-second bound is correct once the path works, so it is unchanged. Local Podman 5.4.2 (pasta networking) does not reproduce the stall.
  - Fix: a new `reverse_broker.py wait-worker-broker` gate. It reads the worker metrics and waits up to 180 seconds (a harness bound covering the observed 70-second stall) until the worker completes three consecutive reverse reconciliations, each making at least one authenticated broker exchange, with no new runtime fault and with ready, fault-free gauges. `run_reverse_lifecycle` runs it first in both profiles, before the broker TERM or worker pause, and prints the wait time and faults seen as evidence. Unit tests cover the gate, its fault reset, timeout and CLI; a harness test proves the ordering and fails without the gate.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-695.
