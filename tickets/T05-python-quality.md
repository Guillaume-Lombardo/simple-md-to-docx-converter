---
ticket: T05
linear_id: G1L-315
linear_url: https://linear.app/g1lom/issue/G1L-315/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T05 - Configure Python quality and coverage

## Objective

Configure Ruff, ty, Pytest, pytest-cov, the pytest-mock restriction, 90% thresholds, and changed-line coverage.

## Acceptance criteria

- The implementation satisfies the T05 outcome in `docs/product-specification.md`.
- Automated tests cover all behavior introduced by this ticket.
- Every feature in scope that crosses a real boundary includes integration coverage for its primary successful path and every relevant failure behavior.
- Every delivered user-visible or operational workflow in scope includes E2E coverage against the final rootless image for its primary path and every relevant critical failure, authorization, cancellation, recovery, or concurrency behavior.
- Any integration or E2E exception is justified explicitly in the pull request and approved explicitly by a reviewer.
- The canonical formatting, linting, type-checking, and applicable test commands pass.
- Documentation and user-facing text are in English.
- Both storage profiles are considered when the shared contract is affected.
- Security and rootless-runtime requirements are verified when applicable.

## Dependencies

- T01
- T03

## Progress

- 2026-08-23: Started implementation after verifying project, team, priority, scope, acceptance
  criteria, and dependency parity with Linear G1L-315. T01 and T03 are both `Done`; T05 has no
  remaining blocker. Work is isolated on `chore/T05-python-quality` from `main` at `58d27a1`.
- 2026-08-23: Configured Ruff for Python 3.14 correctness, security, modernization, import, and
  maintainability policies; configured `ty` to check `src`, `scripts`, and `tests` against Python
  3.14; retained every required Pytest marker; and centralized blocking 90% application branch
  coverage in the canonical Pytest configuration.
- 2026-08-23: Enforced the pytest-mock policy twice: Ruff bans `unittest.mock`, while the existing
  AST-based CI validator remains a fail-closed static backstop. Narrow Ruff exceptions cover only
  reviewed subprocess vectors and test assertions/process probes.
- 2026-08-23: Added a shell-free changed-line checker for pull requests and merge-group candidates.
  It compares reviewed commits with a zero-context Git diff, measures changed executable lines only
  under `src/md_converter`, requires 90%, rejects changed application files missing from Coverage.py
  data, and passes changes with no executable application lines. The T03 light job produces the
  report; the stable `CI / gate`, action pins, permissions, cache policy, timeouts, draft behavior,
  domain lifecycle, and both future storage-profile domains remain unchanged.
- 2026-08-23: Added unit policy/parser/configuration tests and real-process integration tests for
  Git success at 90%, coverage failure at 80%, missing Git references, allowed Ruff imports, and
  rejected `unittest.mock` imports. The active `ci-infrastructure` domain now executes these tests
  alongside the existing shell-free runner boundary and explicitly disables nested coverage because
  the light job owns application coverage enforcement.
- 2026-08-23: All 54 default and full tests pass with 100% application branch coverage. Locked
  synchronization, Ruff format/lint, `ty`, both canonical Pytest commands, the 47-test unit/light
  suite, actual-branch changed-line evaluation, `uv lock --check`, the CI security validator,
  checksum-verified actionlint v1.7.12 with ShellCheck, and `git diff --check` all pass.
- 2026-08-23: T05 changes repository quality and CI policy rather than storage behavior, product
  runtime, or a final application-image workflow. Storage-profile parity, rootless validation, and
  final-image E2E are therefore not applicable; independent review must confirm this assessment.
  Hosted pull-request and main-run verification remain pending publication, so T05 stays
  `In Progress` until the reviewed change is verified on `main`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
