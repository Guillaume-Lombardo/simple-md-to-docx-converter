---
ticket: T02
linear_id: G1L-313
linear_url: https://linear.app/g1lom/issue/G1L-313/
status: Done
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
- 2026-08-23: During bootstrap, required status checks intentionally remained unset until T03
  published the single `CI / gate`, allowing its exact name and application identity to be observed
  before protection was tightened without a missing-check deadlock.
- 2026-08-23: Live GitHub API reads provide integration evidence for the repository-policy boundary.
  No application runtime behavior or final-image workflow is introduced, so Python integration and
  final-image E2E coverage are not applicable. The pull-request reviewer must confirm this
  applicability assessment.
- 2026-08-23: All canonical checks passed from the T02 worktree: locked dependency synchronization,
  Ruff formatting and linting, `ty`, the default and full Pytest suites, `uv lock --check`, and
  `git diff --check`.
- 2026-08-23: Independently reviewed policy record PR
  [#10](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/10), exact head
  `844507e8b675f3014a8b124f8d27e8587305a24c`, was squash-merged and verified on `main` as
  `684a45cec000c1f8f184fdf964d585c97ea6366e`.
- 2026-08-23: After independent review of T03 PR #12, squash merge
  `4c36f4f65aef0f1008f7b0bd4f5fc22237387536` was verified on `main`. Push run
  [32644131962](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/32644131962)
  succeeded on that exact SHA with all five jobs green: `CI / detect domains` (`97205618867`),
  `CI / light` (`97205618812`), `CI / affected domains` (`97205642804`), non-skipped
  `CI / ci-infrastructure` (`97205642792`), and the single `CI / gate` (`97205683348`).
- 2026-08-23: Final live GitHub API verification confirms strict required status checks with exactly
  `{context: "CI / gate", app_id: 15368}` from `github-actions`; pull requests, administrator
  enforcement, resolved conversations, linear history, and force-push/deletion prevention remain
  enabled. Squash is the only merge method, merged branches are deleted automatically, and auto
  merge remains disabled.
- 2026-08-23: The repository remains single-developer, so native approving reviews stay at zero,
  with stale-review dismissal, code-owner review, and last-push approval disabled. Independent
  agent review is mandatory operationally, and the orchestrator serializes merges because this
  user-owned public repository cannot use GitHub merge queue.
- 2026-08-23: Live GitHub API reads and successful GitHub Actions runs cover the repository-policy
  operational boundary. T02 introduces no application behavior or final image, so application-image
  E2E coverage is not applicable; independent review accepted this applicability assessment. Every
  T02 acceptance criterion is verified and no T02 limitation remains.
- 2026-08-23: The closure mirror repeated locked synchronization, Ruff formatting and linting,
  `ty`, 39 default and full tests, 98.21% unit/light branch coverage, both real subprocess
  integration outcomes, `uv lock --check`, the CI security validator, checksum-verified actionlint
  v1.7.12 with ShellCheck, and `git diff --check`; every check passed.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
