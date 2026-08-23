# Orchestration State

Last verified: 2026-08-23

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Current State

- T01 is Done on `main`; T02 is merged on `main` but remains In Progress until T03 publishes the
  first successful `CI / gate` and that observed check is required strictly.
- T03 is staged In Progress on `ci/T03-selective-github-actions`; its formal T02 dependency remains
  recorded because this continuation exists only to break the status-check bootstrap cycle.
- Linear project status: In Progress. T00, T02, T03, and T04 are In Progress; T01 is Done; remaining
  tickets are in Backlog.
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
- Live GitHub settings now protect `main`: pull requests, admin enforcement, resolved conversations,
  linear history, no force push or deletion, squash-only merges, and automatic deletion of merged
  branches. GitHub approval count remains zero in the single-developer phase; independent agent
  review is enforced by the orchestrated workflow without deadlocking the sole collaborator.

## Blockers and Risks

- T00 still needs approved engine sources, a Chrome/OpenShift sandbox design, and Podman/OpenShift
  validation; this does not block T02.
- Automatic relaunch of a stopped thread requires an external supervisor.
- `CI / gate` cannot be required until T03 creates and successfully publishes that exact check;
  requiring it earlier would deadlock every pull request.
- GitHub merge queue is unavailable because this public repository is user-owned, so the
  orchestrator must serialize merges.

## Next Actions

1. Validate and independently review T03 without changing GitHub settings.
2. Publish a ready, non-draft T03 pull request, observe the successful `CI / gate` check and its
   GitHub Actions application identity, then require that exact check strictly on `main` for T02.
3. Before each task, verify repository and Linear state; delegate implementation and independent
   review to separate workers.
4. After each worker, merge, interruption, or blocker, rewrite this file to describe only the
   current state.

## Validation

- Last known project update reports canonical checks passing on `main`.
- T02 live-state verification confirms all current merge controls; required status checks are
  intentionally null until T03.
- T03 local validation passes all canonical checks, 39 tests, 98.21% unit/light branch coverage,
  the active CI-infrastructure subprocess suite, lock and workflow security validation, and
  checksum-verified actionlint with ShellCheck. Independent review's gate-enforcement and cache
  findings are fixed locally; the GitHub-hosted `CI / gate` and Actions application identity remain
  pending publication.
- Final product validation: not run; the product is incomplete.
