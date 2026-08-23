# Orchestration State

Last verified: 2026-08-23

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Current State

- T01, T02, T03, and T05 are Done and verified on `main` at `4b93725`. Linear G1L-315
  intentionally remains In Progress until this closure mirror is merged and verified.
- Linear project status: In Progress. T00 and T04 remain In Progress; remaining tickets are in
  Backlog.
- T06 is the next delivery ticket, but implementation must wait for the PM decision on the
  unresolved Argon2id/local-account/session policy and final-image E2E sequencing.
- The product is not functional yet: API, conversion, storage, queue, UI, and final image remain.
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
- Live GitHub settings now protect `main`: pull requests, admin enforcement, resolved conversations,
  linear history, strict `CI / gate` from GitHub Actions app `15368`, no force push or deletion,
  squash-only merges, and automatic deletion of merged branches. GitHub approval count remains zero
  in the single-developer phase; independent agent review is enforced by the orchestrated workflow
  without deadlocking the sole collaborator.

## Blockers and Risks

- T00 still needs approved engine sources, a Chrome/OpenShift sandbox design, Podman/OpenShift
  validation, and the authentication parameters needed to unblock T06.
- T06 also needs a PM decision on whether its final-image E2E coverage is advanced into T06 or
  explicitly sequenced with T20/T21.
- Automatic relaunch of a stopped thread requires an external supervisor.
- GitHub merge queue is unavailable because this public repository is user-owned, so the
  orchestrator must serialize merges.

## Next Actions

1. Independently review and merge the T05 closure mirror, verify `main`, then mark G1L-315 `Done`
   and re-fetch it for parity.
2. Obtain the PM decisions required for T06, then synchronize and start G1L-316.
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
- Live GitHub API/Actions verification covers the T02/T03 operational boundary. Final application-
  image E2E is not applicable to repository CI/protection and independent review accepted that
  assessment.
- Final product validation: not run; the product is incomplete.
