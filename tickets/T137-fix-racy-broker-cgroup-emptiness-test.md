---
ticket: T137
linear_id: G1L-690
linear_url: https://linear.app/g1lom/issue/G1L-690/t137-fix-the-racy-cgroup-emptiness-assertion-in-the-rootless-podman
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T137 - Fix the racy cgroup emptiness assertion in the rootless Podman broker integration test

## Problem

`tests/integration/broker/test_podman_runtime_integration.py::test_real_rootless_podman_whole_unit_lifecycle_and_proof`
failed intermittently in the hosted `CI / container` job. It failed in run 38298497346 on PR #300
head `62d72fc`, and passed in the same job at the same head in run 38296367409.

The test reads `cgroup.events` immediately after `runtime.hard_terminate(...)` and expects
`populated 0`. The kernel updates the cgroup-v2 `populated` flag asynchronously after SIGKILL, so
the read can still see `populated 1`:

`AssertionError: assert 'populated 1\nfrozen 0\n' == 'populated 0\nfrozen 0\n'`

The failed test leaves its managed unit container behind. Three later tests in the same module
then fail at broker start with `Broker reconciliation is incomplete`, caused by
`Podman container specification is invalid`, because reconciliation discovers the leftover unit:

- `test_runtime_deadline_survives_broker_and_inventory_shutdown`
- `test_orphan_restart_sweep_refuses_readiness_on_identity_mismatch`
- `test_persisted_empty_proof_reconstructs_removal_without_event_history`

No application code changed in the failing run; T135 does not touch `src/` or the broker tests.

## Scope

- Make the post-kill emptiness assertion wait, with a short bound, for the kernel to report
  `populated 0`, instead of reading once. A persistent `populated 1` must still fail.
- Determine whether the product itself relies anywhere on immediate emptiness after
  `hard_terminate`. `confirm_empty` already re-reads and refuses a non-empty unit; verify that its
  callers retry rather than treat a transient non-empty state as a terminal failure. If they do
  not, fix the product path and add a test.
- Make each test in the module clean up its managed units even when it fails, so that one failure
  cannot cascade into the others.

## Acceptance criteria

- The emptiness assertion is bounded and fails on a persistent non-empty cgroup. A test proves
  this.
- A failure in one test of the module leaves no managed container that breaks the next test's
  reconciliation.
- The `container` domain passes in three consecutive hosted runs.
- The canonical checks pass.

## Dependencies

- None. Found during the T135 repeated-run evidence.

## Root cause

The test reads the cgroup of the per-unit systemd slice (`markweavet70<unit>.slice`), not only the
container scope. Podman places the container in `libpod-<id>.scope` and its conmon monitor in
`libpod-conmon-<id>.scope`, both inside that slice. After `podman kill`, conmon reaps the
container, runs its exit command (`podman container cleanup`) and only then exits. A local
measurement on 2026-10-12 (rootless Podman 5.4.2, systemd cgroup manager, runc) showed the
container scope empty about 40 ms after the kill, `podman kill` returning after about 115 ms, and
the slice still populated until about 260 ms. Podman therefore reports the unit as `exited` while
the slice is still legitimately populated by conmon. On a slower hosted runner the single read
after `hard_terminate` landed inside that window.

## Product conclusion

The product is exposed to the same race, although the failing run did not observe it there.
`PodmanIsolationRuntime.confirm_empty` read `cgroup.events` once and raised on `populated 1`. Its
only caller, `IsolationBrokerService._terminate_and_prove`, runs `hard_terminate`, `confirm_exit`
and `confirm_empty` back to back, with only a few Podman inspections in between. A transient
`populated 1` turned a successful termination into `TERMINATION_UNPROVEN`, and `_fail` cleared
broker readiness for every principal. The unit stayed durably `EXIT_CONFIRMED` and a later
reconciliation completed it, so the defect was fail-closed. It was still a real availability defect:
the worker discarded a successful conversion and the broker refused all requests until the next
reconciliation sweep. The Kubernetes backend already retries bounded `not_ready` emptiness
observations (`docs/kubernetes-reverse-isolation.md`). The Podman backend did not.

Fix: `confirm_empty` still checks the Podman runtime state once. It then re-reads the slice's
`populated` flag through `CgroupEmptinessWait`, a frozen, validated bound of five seconds with
50 ms polls by default, configurable on the runtime constructor together with the injected
clock. Only a well-formed `populated 1` is retried. Invalid evidence fails at once, and a slice
that stays populated past the bound raises `Podman stable unit cgroup is not empty` as before.

## Progress

- 2026-10-12: Created in Linear and mirrored here. Work started on branch
  `fix/T137-broker-cgroup-race` from `main` `9306cab`.
- 2026-10-12: Reproduced the timing locally and fixed both sides:
  - Product: `src/markweave/broker/podman_cgroups.py` adds `CgroupEmptinessWait` and
    `_await_unpopulated`. `PodmanIsolationRuntime.confirm_empty` uses them. Unit tests in
    `tests/unit/broker/test_podman_runtime.py` cover a transient populated slice (same evidence
    as an immediate empty read), a persistent populated slice (fails after exactly the bound,
    with clamped pauses), invalid evidence (no retry), a running unit (no wait) and policy
    validation. A new real rootless Podman integration test places a foreign process in the unit
    slice with `systemd-run --user --slice=...`. It proves that `confirm_empty` refuses after the
    bound, and that the broker then proves and removes the unit once the member stops.
  - Test: `tests/integration/broker/cgroup_events.py` adds a bounded poll (five seconds, 20 ms
    polls) that returns the last observation for the caller's unchanged exact assertion. The poll
    is unit-tested in `tests/unit/broker/test_cgroup_events_polling.py`, including the
    persistent-mismatch failure. A mutation that disables the poll fails three of those tests.
  - Isolation: an autouse fixture in the Podman module removes every managed container and stops
    every unit slice after each test, even when the test fails. An injected failure in the
    lifecycle test, which left a running managed container behind, no longer affected the other
    eight tests.
  - `docs/reverse-broker-deployment.md` documents the bounded emptiness wait.
- 2026-10-12: Opened PR #301. Local checks:
  - `ruff format --check`, `ruff check` and `ty check` pass.
  - `tests/unit/broker/`: 1,040 passed.
  - The real rootless Podman module passed 9 of 9 in five consecutive runs.
  - The default canonical command: 6,051 passed and 8 failed, with 94.36% total coverage, 100% of
    changed application lines and 90.48% branch coverage. The 8 failures are in the untouched
    `tests/integration/test_local_test_services_interruption.py`, which passes alone (15 passed).
  - The three consecutive hosted `container` runs are still pending.
- 2026-10-12: Review round 1 (two findings):
  - Minor finding, fixed. `_await_unpopulated` still accepts the first read immediately. A later
    observation now counts only when both the pause and the read finished before the deadline.
    New injected-clock unit tests cover an overslept pause followed by `populated 0`, a read that
    finishes after the deadline, and a slow populated first read; all three raise the not-empty
    error.
  - Major finding, step (a) done. Two new real rootless Podman integration tests drive
    `service.terminate` while a foreign `systemd-run --user --slice=<unit slice>` member shares
    the slice:
    - Transient member: the test releases the member only once the container's own scope is
      empty. The broker observes the populated slice, retries, proves termination and keeps
      readiness. Durable transitions are `EXIT_CONFIRMED`, `EMPTY_CONFIRMED`, then removal.
    - Persistent member: `service.terminate` raises `TERMINATION_UNPROVEN` after the bound, and
      readiness is cleared. The unit stays `EXIT_CONFIRMED` with no proof, including after a
      refused sweep. Once the member exits, `service.start()` proves emptiness, removes the unit
      and restores readiness.
    - Both tests fail when the product reverts to a single read.
    - These two tests replace the earlier runtime-only persistent-member test.
  - Major finding, step (b), final-image E2E: started after T135 (PR #300) landed on `main` as
    `0e0f7f3`. `origin/main` was merged into the branch; nothing was rebased or force-pushed.
  - Local checks: ruff and ty pass. Broker unit tests: 1,053 passed. Changed application lines
    are 100% covered (31/31). The Podman module passed 10 of 10 in eight consecutive runs.
- 2026-10-12: Final-image E2E and dispatch deadline.
  - Scenario. `scripts/e2e/reverse_broker.py watch-member --mode transient|persistent` is a
    prearmed observer. It binds to the single new managed unit and starts a
    `systemd-run --user --slice=<unit slice>` member. It then re-reads the signed inventory: the
    unit must still be before `EXIT_CONFIRMED`, which proves that the member is in place before
    every emptiness read. The observer refuses any emptiness proof while the member is alive.
    `tests/e2e/reverse_lifecycle_workflow.py prepare-member` / `verify-member` submit one real
    reverse job and verify its outcome. `run_reverse_member_fault` runs both modes in the
    `reverse-lifecycle` phase (shard 2).
  - Transient expectations: first-attempt publication, no new broker fault, readiness kept.
  - Persistent expectations: the worker records a new `termination_unproven` fault and no
    `transport_failure`. The job publishes only after reconciliation proves the released slice
    empty and recovery runs. In both modes the observed unit must match attempt 1's `unit_id`
    and `proof_unit_id`, and the proof must be recorded after the member is released.
  - Known limit, not an exception. For the transient member the E2E proves the outcome: no proof
    while the member is alive, first-attempt publication and readiness kept. It cannot prove that
    the broker's first emptiness read came before the release, because the broker exposes no
    signal for its reads. The real rootless integration test proves the retry itself.
  - The first local E2E run found a regression introduced by this branch. Both transports treat a
    dispatch that outlives `transport.operation_timeout_seconds` as fatal (`_watch_dispatch`). The
    fixed five-second wait, plus the other termination steps, exceeded the E2E's five-second
    deadline: the broker stopped serving, and a persistent member crashed the broker instead of
    producing a clean `TERMINATION_UNPROVEN`.
  - Owner decision (2026-10-12): optional configuration with a derived default.
    - The `podman` object accepts optional `cgroup_emptiness_timeout_seconds` and
      `cgroup_emptiness_poll_interval_seconds`, named after the Kubernetes
      `attester_readiness_*` keys.
    - When absent, the timeout is `min(5 s, transport.operation_timeout_seconds / 4)` and the poll
      interval is 50 ms, capped by the timeout.
    - A configured timeout above a quarter of the transport operation timeout, a poll interval
      above the timeout, and non-positive or non-finite values are rejected.
    - The templates enumerate only required keys and are unchanged. Existing configurations keep
      working. `docs/reverse-broker-deployment.md` documents the rule.
  - Regression test. A new real rootless Podman integration test runs real `UnixBrokerServer`
    and `MtlsBrokerServer` instances with a four-second operation timeout. A persistent member's
    terminate returns `TERMINATION_UNPROVEN` after the one-second bound and within the deadline.
    The server is neither failed nor stopping and still answers `READY` (not ready). After the
    member exits, reconciliation restores readiness and the unit reaches `REMOVED`. With the old
    five-second wait, both transports fail with `_watch_dispatch` stopping the server.
  - Verification (2026-10-13), on images built with `scripts.container.ci_images build`:
    - `e2e-standalone-2` passed in 7 min 26 s: reverse-lifecycle 88 s, browser-workspaces 181 s,
      browser-runtime 151 s.
    - `e2e-distributed-2` passed in 7 min 48 s: reverse-lifecycle 90 s, browser-workspaces 186 s,
      browser-runtime 161 s.
    - The real rootless Podman module passed 12 of 12 in three consecutive runs.
    - `ruff format --check`, `ruff check`, `ty check`, `scripts.ci.e2e_shards` and `validate_ci`
      all pass.
    - The default canonical command: 6,186 passed and 8 failed, with 94.37% total coverage. The 8
      failures are again only in the untouched `test_local_test_services_interruption.py`, which
      passes alone (15 passed).
    - Changed application lines are 100% covered (57/57); branch coverage is 90.53%.
- 2026-10-13: Review round 2 (two findings). Hosted CI run 38314831565 passed at `b46e272`.
  - Major finding, fixed: the emptiness wait got a fresh quarter-timeout bound even after the
    kill and inspections had used most of the dispatch. A slow command followed by the wait could
    still trip the fatal watchdog.
    - New `src/markweave/broker/operation_deadline.py`. Each Unix and mTLS dispatch publishes an
      absolute budget equal to the dispatch deadline minus a tenth of
      `transport.operation_timeout_seconds`; the tenth is kept for the response and for reaping a
      cancelled command.
    - `BoundedCommandRunner` clamps every Podman and `systemctl` command to that budget and
      refuses to start one once the budget is spent.
    - `PodmanIsolationRuntime._call` refuses commands once the budget is spent, which also covers
      injected command doubles.
    - The emptiness wait is shortened to the time remaining.
    - This follows the Kubernetes attester precedent, which bounds each exchange by
      `min(request timeout, remaining readiness time)`.
    - The owner-approved optional keys and the quarter ceiling remain the policy limits.
    - Dispatches outside a transport, such as startup reconciliation, keep their previous bounds.
  - Regression coverage on both transports, against real rootless Podman. A slow-podman wrapper,
    run through the real `BoundedCommandRunner`, delays one subcommand. Scenarios: a persistent
    slice member, a 3.4 s kill, 1.3 s inspections, and a removal delayed past the budget.
    - Each scenario returns `TERMINATION_UNPROVEN` within the 4 s deadline; the server is neither
      failed nor stopping and still answers `READY`.
    - After the delays are removed, reconciliation completes the termination, restores readiness
      and leaves the unit `REMOVED` with its proof.
    - With the budget disabled, the three slow-command scenarios fail through the watchdog.
    - Unit tests cover the budget scope, the runner's clamp and its refusal before spawning, the
      runtime's refusal, and the clamped wait. Transport tests on both servers prove that a
      dispatch observes a budget below 90% of its deadline.
  - Minor finding, fixed: `tests/integration/broker/cgroup_events.py` now accepts a later
    observation only when both its pause and its read finished within the bound. Deterministic
    tests cover an overslept pause and a read that finishes after the bound.
  - Verification (2026-10-13), after the round-2 fixes, with images rebuilt through
    `scripts.container.ci_images build` because every broker dispatch is now budgeted:
    - `e2e-standalone-2` passed in 8 min 14 s: reverse-lifecycle 96 s, browser-workspaces 191 s,
      browser-runtime 179 s.
    - `e2e-distributed-2` passed in 8 min 34 s: reverse-lifecycle 98 s, browser-workspaces 192 s,
      browser-runtime 191 s.
    - The real rootless Podman module passed 18 of 18 in three consecutive runs.
    - `ruff format --check`, `ruff check`, `ty check`, `scripts.ci.e2e_shards` and `validate_ci`
      all pass.
    - The default canonical command: 6,197 passed and 8 failed, with 94.36% total coverage. The 8
      failures are only in the untouched `test_local_test_services_interruption.py`, which passes
      alone.
    - Changed application lines are 100% covered (99/99); branch coverage is 90.49%.
- 2026-10-13: Review round 3 (one major finding). The 10% response reserve was smaller than the
  one second `BoundedCommandRunner._terminate` could spend reaping a killed command. A command
  that exhausted its budget and was reaped 600 ms late tripped the fatal watchdog.
  - Fix: inside a dispatch budget, a command now stops executing early enough to keep
    `min(1 s, 10% of its remaining time)` for reaping. Its kill-and-reap wait is bounded by the
    budget end instead of a fixed second. If reaping is not confirmed in time, the runner raises
    `Podman command cleanup failed` (fail closed). Outside a dispatch (startup reconciliation),
    the previous contract is unchanged. The inventory's SQLite busy timeout is also clamped to the
    remaining budget.
  - Regression coverage:
    - Unix and mTLS transport tests (no Podman, run in the default suite): a command exhausts its
      execution budget and its reaping is delayed by 600 ms. The dispatch returns its response
      within the 2 s deadline, with `cleanup failed`, and the server keeps serving. With the
      previous one-second reap window, both fail through the watchdog.
    - A new `unreaped-kill` case in the real rootless Podman matrix covers both transports.
    - Unit tests cover the reap window and the fail-closed path.
  - Why no remaining path can exceed the dispatch deadline `D`:
    1. The transport sets the budget `B = D - T/10` (where `T` is the operation timeout) in the
       handler thread before calling the dispatcher. All service and runtime work for that
       request runs synchronously in that thread under `B`. The watchdog thread does no runtime
       work.
    2. Every external process goes through `BoundedCommandRunner`.
       - It refuses to spawn once `B` has passed.
       - Its blocking points during execution (`select`, `process.wait`) use an execution deadline
         at or before `B` minus the reap slice. Writes are non-blocking, and reads happen only on
         descriptors that `select` reported ready.
       - Kill and reap are bounded by `B`, and closing the pipes does not block.
       - Unconfirmed reaping raises instead of waiting.
    3. `PodmanIsolationRuntime._call` also refuses commands after `B`, which covers injected
       command doubles. The emptiness wait ends by `min(policy bound, B)`.
    4. The remaining runtime work does not block beyond bounded local I/O: cgroupfs reads and
       stats, cgroup `mkdir`/`rmdir`, and bounded in-memory archive and JSON handling.
       - Inventory writes use WAL with a single writer, which holds the broker's exclusive
         authority lock, so writers never wait for readers.
       - The SQLite busy timeout is nevertheless clamped to `B`, and an expiry is an ordinary
         `INVENTORY_FAILURE`.
    5. After the runtime returns or raises, the service and dispatcher only map errors and build
       fixed, bounded responses. The `T/10` reserve covers this and the transport's encoding.
       Sending the response is bounded by `D` but is not fatal.
    6. Every expiry is an ordinary error: `TERMINATION_UNPROVEN`, `RUNTIME_FAILURE` or
       `RECONCILIATION_INCOMPLETE`, which clears readiness. The durable state machine advances
       only on positive evidence, so a refused step never reports a unit as proven or removed.
       The next reconciliation resumes from the durable state.
  - Pre-existing and unchanged limits:
    - Waiting for the transport's dispatch gate remains fatal by existing design, and this
      change does not alter it. A queued request can still expire while another dispatch holds
      the gate. Budgets make every holder release the gate a tenth of `T` earlier than before.
    - A stall of the whole broker process, such as a host freeze or `SIGSTOP`, is outside any
      in-process bound.
  - Verification (2026-10-13):
    - The real rootless Podman module passed 20 of 20 in three consecutive runs.
    - `ruff format --check`, `ruff check`, `ty check`, `scripts.ci.e2e_shards` and `validate_ci`
      all pass.
    - The default canonical command: 6,202 passed and 8 failed, with 94.37% total coverage. The 8
      failures are only in the untouched `test_local_test_services_interruption.py`, which passes
      alone.
    - Shard 2 was not rerun for this round: no E2E scenario changed, and ordinary E2E commands are
      far shorter than their budget.
    - Changed application lines: 117 of 118 were covered in the full run. The remaining line, the
      pipe-less reaping path, is now covered by a dedicated unit test. Branch coverage is 90.47%.
