# Orchestration State

Last verified: 2026-08-23

## Goal

Deliver the secure asynchronous Markdown-to-DOCX/PDF service defined in
`docs/product-specification.md` through tickets T00-T23.

## Current State

- T01 is Done on `main`; T02 is the next ready foundation ticket.
- Linear project status: In Progress. T00 and T04 are In Progress; T01 is Done; remaining tickets
  are in Backlog.
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

## Blockers and Risks

- T00 still needs approved engine sources, a Chrome/OpenShift sandbox design, and Podman/OpenShift
  validation; this does not block T02.
- Automatic relaunch of a stopped thread requires an external supervisor.

## Next Actions

1. Start T02 and protect `main` with the reviewed merge workflow.
2. Before each task, verify repository and Linear state; delegate implementation and independent
   review to separate workers.
3. After each worker, merge, interruption, or blocker, rewrite this file to describe only the
   current state.

## Validation

- Last known project update reports canonical checks passing on `main`.
- Final product validation: not run; the product is incomplete.
