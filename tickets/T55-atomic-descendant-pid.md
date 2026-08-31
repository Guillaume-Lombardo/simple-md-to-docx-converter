---
ticket: T55
linear_id: G1L-462
linear_url: https://linear.app/g1lom/issue/G1L-462/t55-publish-descendant-pid-probes-atomically
status: Done
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
- `ENGINE_FIXTURE_ROOT` receives a new private child directory for every invocation, and an
  interrupted staged fixture removes only its own temporary PID file.
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
- 2026-08-31: Independent review also identified stale fixture state under `ENGINE_FIXTURE_ROOT`
  and a temporary-file leak if the fixture is terminated before replacement. Every fixture now
  allocates a unique private child directory; the probe-failure staging path proves that only its
  known temporary PID file is removed after process-group termination. Ruff format/check, ty, and
  the three focused process-group cases pass.
- 2026-08-31: Ready PR [#142](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/142)
  was independently approved after CodeRabbit's two threads were resolved. Its exact reviewed head
  `58e7e7b477a4934d863b2f6626f38fda786a232f` passed CI run `33369784787` and was squash-merged
  to `main` as `59b68657b1cde580c386a1f9a3220b923cf8392b`; exact-main CI run `33370113535` passed.
  The GitHub runs covered the real LibreOffice document-engine boundary. Container and final-image
  E2E jobs were not selected and are not applicable because T55 changes only the integration-test
  harness, with no production or container behavior change.

## Coordination

- Status: Done; implementation is verified on `main`.
- Linear remains In Progress until this completion record is published and synchronized.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
or progress changes.
