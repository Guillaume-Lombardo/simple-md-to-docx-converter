---
ticket: T142
linear_id: G1L-695
linear_url: https://linear.app/g1lom/issue/G1L-695/t142-investigate-the-intermittent-t73-diagnostic-binding-readiness
status: Todo
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

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-695.
