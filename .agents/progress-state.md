# Orchestration State

Last verified: 2026-08-23

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Current State

- T01, T02, and T03 are Done and verified on `main` at `4c36f4f`. This closure branch records the
  completed T02/T03 evidence; Linear intentionally remains In Progress until the mirror is merged.
- Linear project status: In Progress. T00 and T04 remain In Progress; T05 is the next ready backlog
  item after T02/T03 closure. Remaining tickets are in Backlog.
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
- Live GitHub settings now protect `main`: pull requests, admin enforcement, resolved conversations,
  linear history, strict `CI / gate` from GitHub Actions app `15368`, no force push or deletion,
  squash-only merges, and automatic deletion of merged branches. GitHub approval count remains zero
  in the single-developer phase; independent agent review is enforced by the orchestrated workflow
  without deadlocking the sole collaborator.

## Blockers and Risks

- T00 still needs approved engine sources, a Chrome/OpenShift sandbox design, and Podman/OpenShift
  validation; this does not block T05.
- Automatic relaunch of a stopped thread requires an external supervisor.
- GitHub merge queue is unavailable because this public repository is user-owned, so the
  orchestrator must serialize merges.

## Next Actions

1. Independently review and merge this T02/T03 closure mirror, then mark G1L-313 and G1L-312 Done
   and re-fetch both issues for final parity.
2. Start T05, the next ready foundation ticket, without waiting for unresolved T00 engine work.
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
- Live GitHub API/Actions verification covers the T02/T03 operational boundary. Final application-
  image E2E is not applicable to repository CI/protection and independent review accepted that
  assessment.
- Final product validation: not run; the product is incomplete.
