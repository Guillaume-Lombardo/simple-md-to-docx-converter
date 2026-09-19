---
ticket: T80
linear_id: G1L-579
linear_url: https://linear.app/g1lom/issue/G1L-579/t80-clean-repository-maintenance-assets-and-configure-local
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T80 - Clean repository maintenance assets and configure local distributed tests

## Objective

Apply the approved repository cleanup from current main and provide reproducible local PostgreSQL/RustFS test services.

## Acceptance criteria

- Move production document-engine assets from spikes/toolchain to toolchain/document-engines; update build, Compose, CI, tests, docs and package-manager isolation references without changing engine bytes or versions.
- Remove obsolete branch-specific T67 CI steps and associated policy/tests while retaining historical migration evidence and recovery support.
- Shorten README by moving advanced quickstart details into an operator guide with working links and preserved warnings.
- Keep the anydoc compatibility adapter until an approved upstream replacement exists.
- Archive unmerged branches and detached worktree tips before deleting obsolete branches/worktrees; preserve dirty work and local-only material.
- Provide disposable, loopback-only PostgreSQL and RustFS services using existing pinned CI images, bounded readiness, isolated test credentials/resources, automatic environment setup and cleanup.
- Verify real distributed tests without missing-environment errors, plus relevant build/path/CI/docs checks; report unrelated failures honestly.

## Dependencies

- T22
- T23
- T67
- T76

## Progress

- 2026-09-19: User authorized cleanup, branch/worktree deletion and local test-service configuration. Started from main a4e90f3. No release, merge or publication is included.

- 2026-09-19: Implemented locally on chore/T80-repository-cleanup, based on main a4e90f3. Moved production engine assets to toolchain/document-engines without changing their bytes or versions; updated active references while preserving historical migration evidence. Removed six obsolete T67 workflow steps and their validator/tests. Shortened README and preserved advanced instructions in docs/quickstart.md. Kept the anydoc adapter.
- Added disposable PostgreSQL/RustFS services with pinned images, random loopback ports and credentials, bounded resources/readiness, automatic fixture setup and teardown, and explicit rejection of partial external configuration. Added unit and real lifecycle failure-path coverage. Corrected CI source discovery to ignore generated pnpm environments.
- Archived all branch tips, detached worktree tips and local-only material before removing 12 obsolete local branches, 17 worktrees and the obsolete remote T48 branch. GitHub now has only main; local branches are main and the working branch. Preserved the primary checkout with unrelated edits. Recovery archive identifier: `markweave-cleanup-20260919T192933Z`; its host-specific location is recorded in ignored local environment notes.
- Validation passed: uv sync --all-groups; Ruff format/check; ty; CI policy validation; frozen pnpm installation/workspace validation; 16 browser tests; 441 targeted Python tests; final 65 service/fixture tests including 47 real PostgreSQL/RustFS tests and real resource cleanup after a failing test body. No test-service containers remained.
- Built the final rootless image and passed document-engine smoke validation, runtime-operations E2E, API smoke and distributed API smoke including termination/recovery.
- Canonical engine-excluded suite: 4286 passed, 6 failed, 45 deselected; coverage 94.86%. Three fixture-call failures introduced by the new dependency were corrected and passed in the final 65-test rerun. The three remaining known failures are release process reaping (T77) and two checkout-mode assertions (T78). The entire 24-minute suite was not repeated after that test-helper-only correction.
- Unrestricted host uv run pytest was not run because Pandoc, Mermaid/Chromium and LibreOffice are absent on the host; real engines were validated in the final image. Nothing was committed, pushed, published or merged. Keep In Progress until reviewed and verified on main.

## Synchronization

PR #236 follow-up: user authorized repairing the distributed E2E readiness deadline. A deliberately stopped RustFS takes approximately 20 seconds to produce HTTP 503, equal to the old outer CLI process deadline. The failure-only probe now uses a bounded 30-second HTTP deadline within a 45-second process deadline; other commands retain 20 seconds. Preserve exit status 1 and the exact not_ready error contract, rejecting network/timeout substitutions. Validation passed: 22 targeted tests, Ruff, ty and CI policy checks. The rootless final-image CLI received and validated a real HTTP 503/not_ready response delayed by 21 seconds. Full two-profile GitHub verification remains pending.

Publication follow-up: the user authorized a ready-for-review pull request, monitoring,
squash merge after successful checks and independent review, and source-branch cleanup.
Completion remains conditional on verification on main. T77/T78 repairs and T73/T50
qualification remain separate work.

Keep Linear and this mirror synchronized. Mark Done only after verification on main.
