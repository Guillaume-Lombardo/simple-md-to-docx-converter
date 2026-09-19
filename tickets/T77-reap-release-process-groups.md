---
ticket: T77
linear_id: G1L-576
linear_url: https://linear.app/g1lom/issue/G1L-576/t77-reap-release-subprocess-groups-reliably
status: Backlog
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

## Synchronization

Keep Linear and this mirror synchronized. Mark Done only after verification on main.
