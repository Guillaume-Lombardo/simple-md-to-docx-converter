---
ticket: T97
linear_id: G1L-617
linear_url: https://linear.app/g1lom/issue/G1L-617/t97-mark-podman-dependent-tests-and-repair-the-default-local-test
status: Backlog
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

- Changing the canonical command edits AGENTS.md and specification section 10; the PR needs explicit owner approval of that operational-rule change.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-617.
