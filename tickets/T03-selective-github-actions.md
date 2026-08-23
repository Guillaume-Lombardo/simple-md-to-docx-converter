---
ticket: T03
linear_id: G1L-312
linear_url: https://linear.app/g1lom/issue/G1L-312/
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T03 - Implement selective GitHub Actions CI

## Objective

Implement selective GitHub Actions workflows, merge_group handling, caching, least privileges, timeouts, and the required CI gate.

## Acceptance criteria

- The implementation satisfies the T03 outcome in `docs/product-specification.md`.
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
- T02

## Progress

- 2026-08-23: Started staged implementation after confirming project, team, priority, scope,
  acceptance criteria, and dependency parity with Linear G1L-312. T02 remains a formal blocker;
  this staged continuation exists only because T02 needs T03 to publish the first `CI / gate`
  check before that check can be required safely on `main`.
- 2026-08-23: Verified the live GitHub repository before implementation. No workflow, workflow run,
  check run, or commit status exists, and `main` has no required status check yet. Existing branch
  protection and merge settings were read but not changed.
- 2026-08-23: Selected one read-only CI workflow with a stable `CI / gate`, conservative path/domain
  detection, draft-light behavior, `merge_group`, `main`, release, manual, and scheduled triggers.
  The weekly Sunday 03:17 UTC schedule is a provisional configurable value pending the T22 usage
  budget decision; release runs validate but do not publish artifacts, which remains T22 scope.
- 2026-08-23: Registered future functional, document-engine, storage-profile, container, and E2E
  domains as explicitly `planned`. The workflow reports affected planned suites and never labels
  them executed; tickets T06/T07/T12/T20/T21 must activate them with their real commands and tests.
- 2026-08-23: Pinned `actions/checkout` v7.0.1, `actions/setup-python` v7.0.0, and
  `astral-sh/setup-uv` v10.0.1 by their live full commit SHAs. The workflow grants only
  `contents: read`, does not read secrets, disables checkout credential persistence, prevents pull
  requests from saving caches, cancels superseded PR/merge-group runs, and bounds every job. Cache
  writes are restricted further to trusted pushes on `main`; forks, merge groups, releases,
  schedules, and manual runs are restore-only.
- 2026-08-23: Added unit tests for path selection, lifecycle enforcement, workflow policy, and the
  shell-free matrix runner, plus real-process integration coverage for successful and failing
  command propagation. All 39 default and full tests pass; the unit/light selection passes with
  98.21% combined branch coverage. Locked synchronization, Ruff format/lint, `ty`, both canonical
  Pytest commands, `uv lock --check`, the local security validator, checksum-verified actionlint
  v1.7.12 with ShellCheck, and `git diff --check` all pass.
- 2026-08-23: Addressed independent review by activating the `ci-infrastructure` domain for every
  detector, runner, registry, workflow, and related integration-test path. Its reviewed command
  runs both real subprocess integration outcomes in `tests/test_ci_runner.py`. Whenever any active
  domain is selected, `CI / gate` now requires the heavy matrix to succeed and rejects a skipped or
  failed result; drafts still remain light-only. Local policy validation also enforces that the
  exact `CI / gate` name occurs once.
- 2026-08-23: Rebased the unpublished branch without conflict onto `main` at `2d1f493` after PR #11
  made repository skills discoverable under `.agents/skills/`. Re-read the updated instructions and
  skills; T03 scope and acceptance criteria are unchanged. Repeated every canonical and targeted
  validation with the same 39 passing tests and 98.21% unit/light branch coverage.
- 2026-08-23: Independently reviewed PR
  [#12](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/12), exact head
  `89912bc21d50f82791704d0ec1e18a14f4b4183f`, was squash-merged as
  `4c36f4f65aef0f1008f7b0bd4f5fc22237387536`; the approved head and squash trees match exactly.
- 2026-08-23: PR run
  [32643329078](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/32643329078)
  passed all five jobs on the approved head: `CI / detect domains` (`97203688829`), `CI / light`
  (`97203688924`), `CI / affected domains` (`97203710557`), non-skipped
  `CI / ci-infrastructure` (`97203710582`), and exactly one `CI / gate` (`97203742700`). Every check
  came from `github-actions` app `15368`.
- 2026-08-23: Main push run
  [32644131962](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/32644131962)
  repeated the full selected CI-infrastructure path successfully on squash SHA `4c36f4f`, including
  the non-skipped integration job (`97205642792`) and single gate (`97205683348`). Live protection
  now requires that exact app-bound gate strictly.
- 2026-08-23: Local and hosted validation covers formatting, linting, types, 39 default/full tests,
  98.21% unit/light branch coverage, both real subprocess integration outcomes, lock consistency,
  the CI security validator, checksum-verified actionlint v1.7.12 with ShellCheck, and clean diffs.
  GitHub API and Actions verification covers the hosted operational boundary.
- 2026-08-23: T03 introduces repository CI rather than application behavior or a final application
  image, so final-image E2E coverage is not applicable; independent review accepted this assessment.
  Planned product domains are intentional activation points owned by their recorded downstream
  tickets, not missing T03 work. Every T03 acceptance criterion is verified and no T03 limitation
  remains.
- 2026-08-23: The closure mirror repeated locked synchronization, Ruff formatting and linting,
  `ty`, 39 default and full tests, 98.21% unit/light branch coverage, both real subprocess
  integration outcomes, `uv lock --check`, the CI security validator, checksum-verified actionlint
  v1.7.12 with ShellCheck, and `git diff --check`; every check passed.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
