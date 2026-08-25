---
ticket: T05
linear_id: G1L-315
linear_url: https://linear.app/g1lom/issue/G1L-315/
status: Done
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
  3.14; retained every required Pytest marker; and centralized blocking 90% overall application
  coverage in the canonical Pytest configuration.
- 2026-08-23: Enforced the pytest-mock policy twice: Ruff bans `unittest.mock`, while the existing
  AST-based CI validator remains a fail-closed static backstop. Narrow Ruff exceptions cover only
  reviewed subprocess vectors and test assertions/process probes.
- 2026-08-23: Added a shell-free changed-line checker for pull requests and merge-group candidates.
  It compares reviewed commits with a zero-context Git diff, measures changed executable lines only
  under `src/markweave`, requires 90%, rejects changed application files missing from Coverage.py
  data, and passes changes with no executable application lines. The T03 light job produces the
  report; the stable `CI / gate`, action pins, permissions, cache policy, timeouts, draft behavior,
  domain lifecycle, and both future storage-profile domains remain unchanged.
- 2026-08-23: Added unit policy/parser/configuration tests and real-process integration tests for
  Git success at 90%, coverage failure at 80%, missing Git references, allowed Ruff imports, and
  rejected `unittest.mock` imports. The active `ci-infrastructure` domain now executes these tests
  alongside the existing shell-free runner boundary and explicitly disables nested coverage because
  the light job owns application coverage enforcement.
- 2026-08-23: Addressed review findings by adding an independent branch-only ratio check based on
  Coverage.py JSON. Both canonical Pytest commands load a fail-closed hook, and the light workflow
  repeats the explicit check before `CI / gate`; exactly 90% passes, a lower ratio fails, and a
  valid internally consistent zero-branch report is defined as 100%. Missing, malformed,
  contradictory, or non-branch reports fail rather than inheriting the combined coverage result.
- 2026-08-23: Hardened every per-file Coverage.py entry before changed-line measurement. Complete
  executed, missing, and excluded line arrays must use unique positive integers, remain disjoint,
  and match statement summary counts. Complete branch arrays, branch summaries, function maps, and
  class maps are also required and checked for consistency. Regression tests reject incomplete
  arrays with nonzero statement totals, inconsistent summaries, invalid lines, duplicate/overlapping
  line or branch sets, and preserve valid excluded/non-executable `0/0` changes.
- 2026-08-23: All 71 default and full tests pass with 100% overall application coverage and valid
  100% zero-branch coverage. Locked synchronization, Ruff format/lint, `ty`, both canonical Pytest
  commands, the 64-test unit/light suite, explicit branch-only and actual-branch changed-line
  evaluation, the 7-test real-process CI-infrastructure suite, `uv lock --check`, the CI validator,
  checksum-verified actionlint v1.7.12 with ShellCheck, and `git diff --check` all pass.
- 2026-08-23: T05 changes repository quality and CI policy rather than storage behavior, product
  runtime, or a final application-image workflow. Storage-profile parity, rootless validation, and
  final-image E2E are therefore not applicable; independent review accepted this assessment.
- 2026-08-23: Independently approved PR
  [#14](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/14), exact head
  `34f4d7c877985b2e7760e0a992c53475b4e79893`, passed hosted pull-request run
  [32646636269](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/32646636269):
  `CI / detect domains` (`97211788589`), `CI / light` (`97211788812`),
  `CI / affected domains` (`97211802774`), non-skipped `CI / ci-infrastructure`
  (`97211802785`), and `CI / gate` (`97211839003`) all succeeded.
- 2026-08-23: PR #14 was squash-merged as
  `4b9372517f83f052f493d999165e1e7902328b1d`; the approved head and squash trees match exactly.
  Main push run
  [32646773360](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/32646773360)
  succeeded on that exact SHA with `CI / detect domains` (`97212125601`), `CI / light`
  (`97212125725`), non-skipped `CI / ci-infrastructure` (`97212141165`),
  `CI / affected domains` (`97212141183`), and the strict `CI / gate` (`97212168599`) from
  GitHub Actions app `15368`.
- 2026-08-23: Every T05 acceptance criterion is verified on `main`; the repository quality gates
  are delivered and no T05 limitation remains. Linear intentionally stays `In Progress` until this
  closure mirror is independently reviewed, merged, and re-read from `main`.
- 2026-08-23: Closure validation repeated locked synchronization, Ruff formatting and linting,
  `ty`, both 71-test canonical Pytest suites at 100% application coverage, the seven-test real
  CI-infrastructure integration selection, `uv lock --check`, the CI validator, and clean diffs;
  every check passed.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
