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
  - Major finding, step (b), final-image E2E: pending. `scripts/e2e/run.sh` is frozen until
    T135 (PR #300) lands.
  - Local checks: ruff and ty pass. Broker unit tests: 1,053 passed. Changed application lines
    are 100% covered (31/31). The Podman module passed 10 of 10 in eight consecutive runs.
