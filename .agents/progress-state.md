# Orchestration State

Last verified: 2026-08-23

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Current State

- T01, T02, T03, T05, and T06 are Done. T06 is delivered and verified, and its ticket mirror and
  Linear G1L-316 are synchronized as `Done`.
- Linear project status: In Progress. T00 and T04 are In Progress; remaining tickets are in
  Backlog.
- T06 was delivered by PR #16: approved head `6f7f89c` was squash-merged as `8e18235`; PR run
  `32650616075` and main run `32650924178` passed, including functional 14/14 and the unique
  app-`15368` protected gate. Closure PR #17 was squash-merged as `4abb4f5`; parity correction PR
  #18 received independent review and hosted CI verification. T12 owns the durable replacement for
  its memory adapters; the approved rootless-image E2E sequencing debt remains T20/T21.
- The authentication API foundation is functional. Conversion, durable storage, queue, worker,
  product UI, and final image remain.
- The original orchestrator thread `01a02e30-fcd1-77a2-9fcf-340fd94c073d` is idle after its first
  turn was interrupted before any worker started.

## Delivered

- Repository specification, Linear mirrors/synchronization, and development rules.
- T01 Python 3.14/`uv` bootstrap and package foundation (PRs #2, #5, #8, and #9).
- Durable orchestration memory plus an ignored local restart prompt (PR #7).
- Partial T00 reproducible engine and rootless-runtime evidence (PR #4).
- T04 integration/E2E policy only; the reference corpus is still missing (PR #3).
- T22 trusted PyPI publication planning only (PR #6).
- T02 protected `main` and established the single-developer review and serialized merge policy
  (PR #10, squash `684a45c`).
- T03 delivered selective read-only GitHub Actions with the single strict `CI / gate`, active
  CI-infrastructure integration domain, and explicit downstream domain lifecycle (PR #12, squash
  `4c36f4f`).
- T05 delivered the Python 3.14 Ruff/ty policy, blocking overall, branch-only, and changed-line 90%
  coverage, pytest-mock enforcement, strict Coverage.py validation, and real Git/Ruff boundary
  tests (PR #14, squash `4b93725`).
- T06 delivered FastAPI configuration and health endpoints, local accounts, sessions, authorization,
  stable errors, and security contracts (PR #16, squash `8e18235`; closure PR #17, squash
  `4abb4f5`).
- Live GitHub settings now protect `main`: pull requests, admin enforcement, resolved conversations,
  linear history, strict `CI / gate` from GitHub Actions app `15368`, no force push or deletion,
  squash-only merges, and automatic deletion of merged branches. GitHub approval count remains zero
  in the single-developer phase; independent agent review is enforced by the orchestrated workflow
  without deadlocking the sole collaborator.

## Blockers and Risks

- T00 still needs approved engine sources, a Chrome/OpenShift sandbox design, and Podman/OpenShift
  validation; these do not block T06.
- T06 final-image rootless E2E is explicitly tracked as approved T20/T21 sequencing debt; unit,
  functional ASGI, and real authentication/HTTP integration coverage remain mandatory in T06.
- Automatic relaunch of a stopped thread requires an external supervisor.
- GitHub merge queue is unavailable because this public repository is user-owned, so the
  orchestrator must serialize merges.

## Next Actions

1. Select T12 as the next ready implementation ticket: T05 and T06 are verified Done. Keep T00 and
   T04 In Progress; T04 still waits on T00 and its reference corpus is not ready.
2. Keep the T06 memory-adapter and final-image E2E debt assigned to T12 and T20/T21 respectively.
3. Before each task, verify repository and Linear state; delegate implementation and independent
   review to separate workers.
4. After each worker, merge, interruption, or blocker, rewrite this file to describe only the
   current state.

## Validation

- Main push run 32644131962 is green at `4c36f4f`; all five implemented CI jobs succeeded.
- T02 live-state verification confirms the strict app-bound `CI / gate` plus every merge control.
- T03 local validation passes all canonical checks, 39 tests, 98.21% unit/light branch coverage,
  the active CI-infrastructure subprocess suite, lock and workflow security validation, and
  checksum-verified actionlint with ShellCheck. PR and main Actions runs independently confirm the
  hosted path and GitHub Actions app `15368`.
- T05 local validation passes all canonical checks, 71 default/full tests, 100% overall application
  and valid zero-branch coverage, 64 unit/light tests, branch-only 90%/sub-90/malformed boundaries,
  changed-line Git 90%/80%/missing-ref/schema boundaries, real Ruff allow/deny import boundaries,
  lock and CI policy validation, checksum-verified actionlint v1.7.12 with ShellCheck, and clean
  diffs. PR run 32646636269 and main run 32646773360 independently passed the non-skipped
  CI-infrastructure domain and strict `CI / gate` on the approved and merged trees.
- T06 local validation passes Ruff formatting/linting, `ty`, locked `uv` sync, both canonical Pytest
  commands with 99/99 tests, 97.58% application coverage (98% rounded), 98.41% changed-line
  coverage, the non-skipped 11-test functional/Argon2id/HTTP domain, the local CI validator,
  actionlint with ShellCheck, and clean diffs. Rootless final-image E2E is the explicit PM-approved
  T20/T21 sequencing exception.
- T06 review-fix validation passes 116/116 default and full tests at 98.70% application coverage;
  96/96 exact light tests at 97.22% application, 93.75% branch, and 97.68% changed-line coverage;
  the 48-test focused race/CSRF/validation/timing/three-identity/selector set; and the non-skipped
  13-test functional/Argon2id/real-HTTP domain. Ruff, `ty`, locked sync, CI validation, actionlint
  with ShellCheck, formatting, and diff checks pass on the committed review-fix tree.
- The last T06 timing re-review finding is implemented: all failed authentication paths have two
  structurally counted current Argon2 work units, and nine-sample real medians range from 29.462 to
  29.866 ms (`max/min=1.014`) without sleeps. Targeted tests pass 24/24, the functional domain
  passes 14/14, and both canonical suites pass 122/122 at 98.73% application coverage. Exact light
  evidence passes 101/101 at 97.28% application, 93.94% branch, and 97.73% changed-line coverage.
- T06 delivery is verified on main: approved head `6f7f89c` was squash-merged as `8e18235`; PR run
  32650616075 and main run 32650924178 both passed, including functional 14/14 and the unique
  GitHub Actions app 15368 `CI / gate`. Security/contract review and the T20/T21 E2E sequencing
  exception were independently approved.
- T06 closure PR #17 was independently approved and squash-merged as `4abb4f5`; main run
  32651297877 passed on that exact SHA. Linear G1L-316 and the ticket mirror are synchronized as
  `Done`; parity correction PR #18 received independent review and hosted CI verification.
- Live GitHub API/Actions verification covers the T02/T03 operational boundary. Final application-
  image E2E is not applicable to repository CI/protection and independent review accepted that
  assessment.
- Final product validation: not run; the product is incomplete.
