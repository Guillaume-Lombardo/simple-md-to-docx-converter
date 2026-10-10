---
ticket: T97
linear_id: G1L-617
linear_url: https://linear.app/g1lom/issue/G1L-617/t97-mark-podman-dependent-tests-and-repair-the-default-local-test
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T97 - Mark Podman-dependent tests and repair the default local test command

## Objective

Make the canonical engine-excluding local test command pass on a host without Podman by marking every test that needs a container runtime, as AGENTS.md requires for unavailable engines.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to change, item 1).

## Acceptance criteria

- Register a `requires_podman` marker (and `requires_docker` if any test needs Docker) in `pyproject.toml` and in specification section 10.
- Mark every test that executes Podman, including `tests/integration/broker/test_broker_process_integration.py`, `tests/integration/broker/test_podman_runtime_integration.py`, `tests/integration/container/test_registry_transport.py` and `tests/integration/test_compose_configuration.py` where applicable. Do not use ad hoc environment checks or unconditional skips.
- Update the canonical command in AGENTS.md, README.md, CONTRIBUTING.md and `docs/local-development.md` to exclude `requires_podman` only; PostgreSQL, S3, slow, integration and E2E tests remain included when runnable.
- Keep CI running the Podman-marked tests in the domains that already run them; prove that no test silently disappears from CI (compare collected test IDs before/after).
- Add a guard test that fails when a test invokes the Podman executable without the marker.
- Reproduce the original failure (`FileNotFoundError: /usr/bin/podman`) on a Podman-free host, then show the canonical command passing there.

## Dependencies

- None.

## Blocking points

- The owner approved the AGENTS.md and specification section 10 canonical-command change on 2026-10-09; the PR still needs the normal independent review.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

- 2026-10-10: Started on branch `fix/T97-mark-podman-tests` from `origin/main`
  `d3dfe628454d62783bdc7ac8c6c9be9e1cdc837e`. Registered `requires_podman` and
  `requires_docker` in `pyproject.toml` and specification section 10. Marked 34 Podman test
  cases: every test in `tests/integration/broker/test_podman_runtime_integration.py` and
  `tests/integration/container/test_registry_transport.py`, and per test (or per parameter)
  those in `tests/integration/broker/test_broker_process_integration.py` that execute Podman
  or launch a broker process that reaches its Podman executable check. Broker tests that are
  rejected while loading configuration stay unmarked and are documented in the guard.
  `tests/integration/test_compose_configuration.py` and
  `tests/integration/test_local_test_services_lifecycle.py` execute the Docker CLI, not Podman,
  and now carry `requires_docker`; they stay in the default command. The default command in
  AGENTS.md, README.md, CONTRIBUTING.md, `docs/agent-workflow.md`,
  `docs/local-development.md` and specification section 10 now also excludes
  `requires_podman`. Three unit tests in `tests/unit/broker/test_process.py` failed on a
  Podman-free host because the broker factory checks the host `/usr/bin/podman` and
  `/usr/bin/systemctl`; an autouse fixture now substitutes temporary executables so these
  unit tests are hermetic. The static unit guard `tests/unit/test_container_runtime_markers.py`
  fails when a test, helper or fixture chain executes Podman or Docker, or launches the
  broker, without the matching marker, and includes negative cases. Collected IDs for every
  CI pytest invocation are unchanged apart from the new guard tests; the default command
  drops exactly the 34 Podman-marked cases. The original `FileNotFoundError: /usr/bin/podman`
  was reproduced on the base commit in a bubblewrap namespace without `/usr/bin/podman`.
  The PR records the canonical-command evidence from that namespace on this branch.
  Limitations: the guard is static and cannot see Podman executed indirectly through shell
  scripts; `test_registry_transport.py` and `test_compose_configuration.py` are not selected
  by any CI pytest invocation, before or after this change.
- 2026-10-10: The Podman-free canonical run on `2df5528` passed: 5,363 passed, 94 deselected,
  exit 0, 94.15% coverage. On a host with Podman, the 34 `requires_podman` cases passed. The
  independent review of PR #281 requested changes, which are now addressed. The guard moved to
  `tests/container_runtime_markers.py` and now:
  - checks per-parameter marks, so a broker exemption covers only the listed configuration-time
    case IDs (a negative test removes the `malformed` case marker and the guard fails);
  - resolves `subprocess`, `asyncio` and `os` module aliases and from-imports, including
    `os.exec*`, `os.spawn*` and `os.posix_spawn*`;
  - follows helpers and fixtures imported from other test-tree modules;
  - lists one reviewed harness call with every reached runtime branch patched, and verifies
    that this entry is still reached.

  The T50 evidence document now states that its command records the T50 revision, and points
  to the current canonical command. Collected IDs for CI invocations are still unchanged apart
  from the 38 guard cases.
- 2026-10-10: Review round 2 on PR #281 found three more guard false negatives, now fixed:
  - `PATCHED_RUNTIME_CALLS` exempts only calls to the reviewed helper from the listed test,
    and only while the required patch is present. Other launches, helper calls and fixtures
    in that test are still checked.
  - Imports now resolve per lexical scope, so function-local imports no longer leak between
    functions.
  - An explicit `executable` (keyword, or `Popen` position 2) now counts as the launched
    program.

  An adversarial pass then added detection for:
  - `shell=True`, `os.system`, `os.popen`, `subprocess.getoutput`/`getstatusoutput` and
    `asyncio.create_subprocess_shell` command strings (every command position);
  - `pty.spawn` and `functools.partial`;
  - `bash -c`, `shlex.split` and list concatenation;
  - `or`/conditional alternatives, `os.path.join` and `Path.as_posix`;
  - constants imported from other test modules;
  - argument forwarding through helper parameters;
  - `self` method helpers, class-level fixtures and `request.getfixturevalue`.

  The remaining limits are documented in the guard module and the PR. The guard has 82 unit
  cases. In the Podman-free sandbox, the affected suites passed (1,368 passed, 34
  deselected). Collected IDs for CI invocations are unchanged apart from the guard cases.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-617.
