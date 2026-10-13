---
ticket: T141
linear_id: G1L-694
linear_url: https://linear.app/g1lom/issue/G1L-694/t141-release-the-broker-handler-slot-before-closing-the-connection
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T141 - Release the broker handler slot before closing the connection

## Objective

`tests/integration/broker/test_unix_broker_transport.py::test_real_workspace_stage_collect_pending_and_success_exchange`
fails intermittently with `ConnectionResetError` on the second or third exchange, never the
first. During T138 it failed in 4 of 6 full parallel (`-n auto`) default canonical runs and in
none of 9 serial runs.

The cause is in `UnixBrokerServer._handle_and_release` in
`src/markweave/broker/unix_transport.py`:

1. The handler's `finally` block calls `connection.close()` before `self._handlers.release()`.
2. The close sends the EOF that completes the client's exchange.
3. The client reconnects immediately. The accept loop can accept that new connection while the
   old handler still holds its slot.
4. With `max_handlers=1`, `acquire(blocking=False)` fails, and the server closes the unread
   connection. The client sees a reset.

Adding a 50 ms delay to the slot release reproduces the failure every time. This is a real
product race, not only a test problem: a client that reconnects right after a response can be
refused whenever every handler slot is busy only because of this ordering.

## Scope

- Release the handler slot before the connection close that completes the exchange, or otherwise
  make slot accounting consistent with what the client observes.
- Keep the bounded-handler admission semantics.
- Check `mtls_transport` for the same pattern and fix it there too.
- Add a deterministic regression test on both transports: inject a delay at the release point,
  then reconnect immediately at the handler limit. The test must fail without the fix.

## Acceptance criteria

- The regression tests pass on both transports and fail without the fix.
- The default canonical command passes in at least three consecutive `-n auto` runs.
- The canonical checks pass.

## Dependencies

- T137 (G1L-690), which changes the same transport modules, merges first. Done: it is on `main`
  at `016bbe7`.

## Blocking points

- None known.

## Progress

- 2026-10-13: Mirrored from Linear and started on branch `fix/T141-broker-handler-slot-release`
  from `origin/main` `c5c0a89`, which contains T137.
- 2026-10-13: Fix.
  - Unix transport: the handler and the accept loop's thread-start failure path release the
    handler slot before the close that the client observes. Nothing that can block runs between
    the release and the close, so the bound on concurrent handlers is unchanged.
  - mTLS transport: the same ordering defect existed for three slots. The response channel
    released its pending-exchange slot and its connection slot only after its TLS close, which
    sends the close_notify that completes the client's exchange. The request channel released
    its handler slot and connection slot only after it published the response to the paired
    channel. A private `_SlotLease` now releases each acquired slot exactly once from any path.
    A delivered response channel first awaits the client's close_notify, with the existing
    deadline and trailing-data check, then releases its slots and only then sends its own
    close_notify, which no longer waits on the peer. The request channel releases its handler
    and connection slots before it publishes the response. Every other path releases the
    connection slot before the close the peer observes.
- 2026-10-13: Regression tests. `tests/integration/broker/slot_release.py` delays every release
  of one server slot by 300 ms.
  - `test_real_unix_reconnect_at_the_handler_limit_is_admitted` (served and rejected first
    exchange) runs consecutive real exchanges at `max_handlers=1`.
  - `test_real_mtls_reconnect_at_each_slot_limit_is_admitted` does the same for the connection,
    pending-exchange and handler slots at a limit of one each.
  - Unit tests prove the release-before-close order on every handler exit path of both
    transports, including the thread-start failure path, and the exactly-once lease.
  - With the source changes reverted, all five integration cases fail with a transport failure
    (connection reset or unexpected EOF), and every new unit test fails.
- 2026-10-13: Other servers with the same accept-and-slot pattern, out of scope here: the
  external-worker metrics server (`_BoundedMetricsHttpServer._process_admitted` in
  `src/markweave/observability.py`) and the Kubernetes node attester
  (`_AttesterHttpServer.process_request_thread` in
  `src/markweave/broker/kubernetes_attester_transport.py`) both call `shutdown_request` before
  releasing their admission semaphore.
- 2026-10-13: Verification at `71892cb` on a 4-CPU machine with no other suite running.
  - `uv sync --all-groups`, `ruff format --check`, `ruff check` and `ty check` pass.
  - Broker suites (`tests/unit/broker` and `tests/integration/broker` under the default marker
    filter, `-n auto`): 1,207 passed.
  - Default canonical command with `-n auto`, three consecutive runs:

    | Run | Result | Time | Coverage |
    |---|---|---|---|
    | 1 | 6,232 passed, 0 failed | 205 s | 94.44% |
    | 2 | 6,232 passed, 0 failed | 198 s | 94.45% |
    | 3 | 6,232 passed, 0 failed | 194 s | 94.44% |

  - Default canonical command, serial: 6,232 passed, 0 failed, in 468 s; coverage 94.44%.
  - Every changed line of both transport modules is covered.
  - Not run: the full `uv run pytest` suite including the Pandoc, Mermaid, LibreOffice and
    Podman markers. The change touches no engine path.
- 2026-10-13: Review round 1 required final-rootless-image coverage of immediate reconnection
  at the admission limit. Merged `origin/main` `961d174` (T139) into the branch.
- 2026-10-13: Final-image scenario design.
  - The final-image E2E broker is the trusted external component. It is host-native, launched
    from the source tree with `markweave.broker.process`, and is not part of any final image.
    The final backend image runs only the worker and its mTLS client.
  - `scripts/e2e/admission_window_broker.py` is a harness-side launcher. It derives the fixture
    broker configuration with `max_handlers`, `max_handshakes` and `max_pending_exchanges` set
    to 1, and delegates the server's connection, pending-exchange and handler slots to wrappers
    that sleep 150 ms before every release. It records every admission decision in a
    content-free report, then runs the unmodified `markweave.broker.process` entry point. No
    production code changes.
  - `run_reverse_admission_window` in `scripts/e2e/run.sh` runs last in the
    `reverse-lifecycle` phase (shard 2), in both profiles. It stops the fixture broker, starts
    the launcher, and runs `tests/e2e/reverse_admission_workflow.py`. `prepare` waits until the
    worker reconnects and submits one real reverse job. `verify` requires a first-attempt
    success with an intact result, zero refused admissions, at least six job exchanges, at
    least one reconnection inside the window, no new worker `transport_failure` fault, and a
    ready, unfaulted worker. The harness then restores the fixture broker.
  - `reverse_broker.py signal` accepts `--admission-report` to signal the exact launcher child.
  - The worker drives every broker exchange from one runner thread, so admission limit one
    never refuses a legitimate exchange with release-before-completion.
- 2026-10-13: The Unix transport has no final-image path today. The worker's Unix client
  requires the broker to run under its own effective UID, and the broker requires the
  client's UID to equal its own. In the rootless arbitrary-UID final-image runtime, the
  container UID maps to a host subordinate UID, so `SO_PEERCRED` cannot match on either side.
  No existing final-image E2E uses the Unix transport. Covering it needs a different
  container user-namespace topology (for example a keep-id mapping) that the harness does not
  have. The Unix fix keeps its source-level regression tests.
- 2026-10-13: Final-image verification at `9c6020c`. The images were built the CI way with
  `uv run python -m scripts.container.ci_images build` from `6032a86`; no image input changed
  after that commit. Image IDs: backend `5795300856…`, frontend `dd55f179a1…`, reverse-attempt
  `c8985add68…`. Each shard ran through `scripts.ci.run_domain` exactly as in CI.

  | Run | Broker ordering | Result | Time | Admission report |
  |---|---|---|---|---|
  | `e2e-standalone-2` | fixed | passed, all three phases | 470 s | 0 refused; 8 exchanges; 12 reconnections inside the window |
  | `e2e-distributed-2` | fixed | passed, all three phases | 477 s | 0 refused; 8 exchanges; 12 reconnections inside the window |
  | `e2e-standalone-2` | old (`c5c0a89` transports) | failed in `prepare` | 122 s | refusals at the limit (28 pending-exchange, 2 connection) |
  | `e2e-distributed-2` | old (`c5c0a89` transports) | failed in `verify`: the job never succeeded | 247 s | 332 pending-exchange and 20 connection refusals |

  - For the old-ordering runs only the host-native broker's two transport modules were reverted.
    The final images, which carry only the worker client, were unchanged. The worktree was
    restored afterwards.
  - The three other reverse-lifecycle scenarios passed in every run before the new one.
  - The real-transport integration test of the launcher also fails on the old mTLS module and
    passes with the fix.
  - Canonical checks at `9c6020c`: `ruff format --check`, `ruff check`, `ty check`,
    `scripts.ci.e2e_shards` and `scripts.ci.validate_ci` pass. The default canonical command
    with `-n auto` passed with 6,274 tests and 94.44% coverage.
  - Not run locally: shard 1 of either profile, which this change does not touch, and the full
    `uv run pytest` run with the engine markers.
- 2026-10-13: Review round 2 approved the change with one minor finding. The admission-window
  launcher ran from the checkout instead of the harness-owned temporary directory, so files
  written relative to the working directory, such as conmon OOM markers, could land in the
  worktree.
  - Fix: the launcher now runs with
    `uv run --directory "$temporary_directory" --project "$repository"`, like every other
    broker launch. The repository is on `PYTHONPATH` because the harness module is not
    installed.
  - A unit test requires every broker launch in `scripts/e2e/run.sh` to run from the harness
    directory.
- 2026-10-13: Decision: the owner approved an exception for the final-image E2E of the Unix
  transport path. The exception is recorded on PR #304 and on G1L-694. The reason is the
  `SO_PEERCRED` UID mismatch in the rootless arbitrary-UID topology described above. The Unix
  fix stays covered by its unit and real-socket integration regressions. The mTLS path keeps
  its final-image admission-window scenario.
- 2026-10-13: Done. Merged as `cd98c07` (PR #304) after three independent review rounds and the owner-approved Unix-path final-image E2E exception; main CI run 38439716364 passed. Follow-up: T145 audits the same release-after-close slot pattern in the metrics server and the Kubernetes attester transport.
