---
ticket: T02
linear_id: G1L-313
linear_url: https://linear.app/g1lom/issue/G1L-313/
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T02 - Protect main and configure the merge workflow

## Objective

Protect main and configure required gates, independent review, merge queue or serialized merge, and squash policy.

## Acceptance criteria

- The implementation satisfies the T02 outcome in `docs/product-specification.md`.
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

## Progress

- 2026-08-23: Readiness audit started after verifying that T01 is `Done` on `main` and that the
  Linear dependency relation matches this ticket.
- 2026-08-23: Audited the live GitHub repository with administrator access. `main` is the default
  and only remote branch, the repository is public and user-owned, no ruleset is configured, and
  GitHub merge queue is therefore unavailable; the orchestrator must serialize merges.
- 2026-08-23: Verified live branch protection on `main`: pull requests are required, administrators
  are included, conversations must be resolved, linear history is required, and force pushes and
  branch deletion are forbidden. There are no bypass allowances or push restrictions. During the
  single-developer phase GitHub requires zero approvals and does not dismiss stale approvals or
  require last-push approval; independent agent review remains mandatory in the orchestrated
  workflow without deadlocking pull requests authored by the sole GitHub collaborator.
- 2026-08-23: Verified the repository merge policy: squash merge is the only enabled merge method,
  automatic branch deletion is enabled, and automatic merge is disabled.
- 2026-08-23: Required status checks intentionally remain unset because T03 has not created or run
  the single `CI / gate` check. The repository currently has no Actions workflow, workflow run,
  check run, or commit status. T03 must publish the exact check through GitHub Actions, observe its
  check name and application identity, require it strictly on `main`, and then verify the resulting
  protection without introducing a missing-check deadlock.
- 2026-08-23: Live GitHub API reads provide integration evidence for the repository-policy boundary.
  No application runtime behavior or final-image workflow is introduced, so Python integration and
  final-image E2E coverage are not applicable. The pull-request reviewer must confirm this
  applicability assessment.
- 2026-08-23: All canonical checks passed from the T02 worktree: locked dependency synchronization,
  Ruff formatting and linting, `ty`, the default and full Pytest suites, `uv lock --check`, and
  `git diff --check`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
