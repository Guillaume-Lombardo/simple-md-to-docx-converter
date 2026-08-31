---
ticket: T55
linear_id: G1L-462
linear_url: https://linear.app/g1lom/issue/G1L-462/t55-publish-descendant-pid-probes-atomically
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T55 - Publish descendant PID probes atomically

## Objective

Eliminate the deterministic test-harness race in the LibreOffice descendant process-group
cancellation integration test.

## Acceptance criteria

- The cancellation probe never treats an empty PID file as a published descendant PID.
- The test harness writes the PID payload before the PID file becomes visible at its final path.
- Regression coverage pauses after an incomplete temporary PID write and proves the final path is
  absent until publication is explicitly released.
- Existing timeout, cancellation, and descendant process-group integration coverage remains intact.
- No production converter behavior changes.

## Dependencies

- T21

## Progress

- 2026-08-31: Started after CI run 33366416225 exposed an empty descendant-PID read between
  `write_text()` truncation and payload write. T55 changes only the LibreOffice integration-test
  fixture and its cancellation assertion.
- 2026-08-31: The fixture now publishes its ASCII PID through a same-directory temporary file and
  `Path.replace()`. The cancellation probe reads the completed value before it requests
  cancellation, so the former empty-file observation fails deterministically. Focused process-group
  coverage passes. Ruff format/check, ty, and the canonical suite excluding unavailable document
  engines pass at 95% coverage.
- 2026-08-31: Independent review required coordinated staging so the atomic-publication assertion
  cannot pass by scheduling luck. The cancellation fixture now pauses with an empty temporary file;
  the probe asserts that the final path is absent, releases publication, and then validates the
  complete final PID. Reverting to a direct final-path write fails at that assertion. Commit
  `542511b` passed Ruff format/check, ty, and all three focused process-group cases.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
or progress changes.
