---
ticket: T77
linear_id: G1L-576
linear_url: https://linear.app/g1lom/issue/G1L-576/t77-reap-release-subprocess-groups-reliably
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T77 - Reap release subprocess groups reliably

## Objective

Diagnose and repair release-command timeout cleanup so descendants are terminated and reaped reliably on both CI and development hosts.

## Acceptance criteria

- Reproduce the audit failure in tests/release/test_process_integration.py: process group could not be reaped instead of the expected timeout.
- Distinguish live descendants from zombies and document supported Linux reaping behavior.
- Preserve bounded deadlines, fixed argv, whole-group termination, and safe errors; do not hide cleanup failures.
- Add real-process integration regressions for timeout, descendant exit, and cleanup failure; validate the release workflow boundary with its applicable E2E tests.
- Run the canonical checks and record any unavailable dependency explicitly.

## Dependencies

- T22

## Progress

- 2026-09-19: Created from the repository audit. Implementation is not part of the repository-cleanup request.

- 2026-09-19: Implementation started in isolated branch `fix/T77-release-process-reaping`.
  Reproduced the audit error under a Linux subreaper that retains orphan zombies; the original
  test passes when the host ancestor promptly reaps them.

- 2026-09-19: Added temporary Linux subreaper ownership, serialized callers, group-specific
  orphan reaping, previous-state restoration, and explicit cleanup errors. Added timeout,
  SIGTERM-resistant descendant, live/zombie exit, unrelated-child, concurrent-caller, syscall
  failure, and portable acceptance-probe regressions. Release guide documents Linux support.
- Validation: `uv sync --all-groups`, Ruff format/check, and `uv run ty check` pass;
  `uv run pytest tests/release --no-cov -q`: 212 passed. Canonical default suite is running;
  unrelated broker integration failures observed so far. Full suite requires unavailable host
  Pandoc, Mermaid CLI, and LibreOffice and remains unverified locally.
- Final-image Linux probe passed with read-only source mount, arbitrary UID 10042:0, read-only
  root, no network/capabilities, 64 PIDs, and 256 MiB. Existing unchanged application image ID
  `d85ed005e46a589e4c8767dadaf18fced34af61c1ef0a04989e70aea9ce862ee`, source label
  `1586bedd7375b54c4799dc686dc04d3af88bea5b`. Release scripts are host tooling, not bundled
  application code; this verifies their behavior on the final-image Linux/Python runtime.
- Independent read-only review started against implementation head `3c1492935f23495ea9e45165fd3c780da60a2cf1`.

## Synchronization

Keep Linear and this mirror synchronized. Mark Done only after verification on main.
