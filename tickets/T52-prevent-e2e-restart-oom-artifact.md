---
ticket: T52
linear_id: G1L-459
linear_url: https://linear.app/g1lom/issue/G1L-459/t52-prevent-final-image-e2e-restart-oom-artifact
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T52 - Prevent final-image E2E restart OOM artifact

## Objective

Prevent the final-image E2E restart workflow from leaving a zero-byte `oom` artifact in the repository working directory, while preserving the existing restart and recovery assertions.

## Acceptance criteria

* Identify and document the root cause of the host-side `oom` marker created immediately after restarting the final-image application container.
* Ensure successful standalone and distributed final-image E2E runs leave the repository worktree unchanged.
* Add deterministic regression coverage for the artifact boundary without weakening forced-restart, OOM, recovery, browser-session, or rootless runtime assertions.
* Verify both standalone and distributed final-image profiles through their complete restart and recovery workflows.
* Keep cleanup bounded to exact harness-owned artifacts and never hide unexpected worktree changes.

## Reproduction

* Reproduced on 2026-08-30 from exact baseline SHA `c1cae3b6ca1d2f8eb6e680eec26f444ea92332c5`.
* The zero-byte `oom` file appears specifically after `podman restart --time 15 "$application_name" >/dev/null` in the final-image application workflow.
* An equivalent neutral container using the same image, `--memory=768m`, `/work` tmpfs, workdir, and restart command does not create the file.
* The complete baseline standalone workflow still passes, confirming an existing harness/application restart side effect rather than a T42 worker regression.

## Dependencies

* T21

## Implementation boundary

* Own final-image E2E harness diagnosis, cleanup, and regression tests.
* Do not change worker decomposition or public job behavior.
* T42 must only record and remove the observed transient artifact; no harness fix belongs in T42.

## Progress

* 2026-08-30: Defect isolated while validating T42. Reproduced against exact baseline SHA `c1cae3b6`; both T42 profiles passed after the transient zero-byte artifact was removed.
* 2026-08-31: Implementation started from exact clean `main` at `5281a745f8974abb6fd09e00504383da2f895510` after verifying T21/G1L-331 is Done. The bounded plan is to instrument both application restart boundaries, reproduce the artifact on current main (and the recorded baseline only if necessary), prove ownership before cleanup, add deterministic regression coverage that fails on unexpected worktree changes, and rerun both complete final-image profiles. T53 browser resource-phase work, worker behavior, public job behavior, and `.gitignore` changes remain out of scope.
* 2026-08-31: Current-main standalone reproduction passed but created `oom` during the first application restart. The file timestamp falls between Podman's restart and container-death events. Conmon 2.1.12 handles a cgroup-v2 `memory.events` `oom` or `oom_kill` counter by writing its persistent marker and a compatibility marker named `oom` relative to its current directory. Podman 5.4.2 starts conmon without setting `cmd.Dir`, so it inherits the repository directory used by the harness's original `podman run`. The application shut down gracefully; the evidence proves a cgroup OOM event, not that PID 1 was OOM-killed. The fix contains every container monitor in the harness-owned temporary directory and fails the run on any before/after worktree delta.
* 2026-08-31: Deterministic regression coverage passes for relative-marker containment, unexpected-change preservation, and complete `podman run` routing. Complete standalone and distributed final-image profiles passed through their browser, provisioning restart, forced SIGKILL/exit-137 recovery, checkpoint restart, snapshot restore, and rootless runtime assertions. Each profile created `oom` only inside its harness-owned temporary directory; successful cleanup removed that exact directory and left Git porcelain unchanged with no repository-root marker or retained success artifacts.
* 2026-08-31: PR #145 follow-up hardens alternate `TMPDIR` handling after review identified that the original `/tmp/tmp.*` guard rejected legitimate `mktemp` output elsewhere. The runner now creates an explicitly prefixed directory, records its device/inode identity, and requires the exact absolute path, prefix, current-user ownership, directory type, non-symlink state, and unchanged identity before every container launch and cleanup. A deterministic alternate-`TMPDIR` test passes with a path containing spaces and proves that the owned tree is removed while a sibling is preserved. The complete standalone profile passed after this change, including all restart and recovery boundaries and final worktree cleanup. During an alternate-`TMPDIR` distributed run, the zero-byte marker was observed at `/tmp/markweave-t52-alt-root.rQw6MH9Z/markweave-e2e.8xptqFBVbU/oom`; failure cleanup removed that exact harness tree while preserving sibling Node/uv cache data and leaving the repository marker-free. Both distributed attempts passed the security-boundary, service, and conversion CLI phases but stopped before the restart boundary on known T53 browser timeouts (`locator.fill` and `page.waitForURL`), so no post-fix complete distributed success is claimed.
* 2026-08-31: A second PR #145 review follow-up removes Bash status masking from `readonly name="$(command)"`. Harness creation and identity acquisition now complete before the outputs become readonly. If identity acquisition fails in the pre-trap boundary, the initializer validates the newly created absolute, prefixed, non-symlink directory and removes it with empty-directory-only `rmdir` before returning failure; normal launch and recursive cleanup retain the current-user ownership and exact-identity checks. Seven deterministic harness tests pass, including actual-runner rejection of relative and symlink `TMPDIR` values before worktree setup and an injected failure during identity acquisition that neither continues nor leaks its new tree. A proportional standalone run under the alternate `TMPDIR` passed build, security-boundary, service, and CLI phases, then stopped before restart on a known T53 browser timeout while waiting for the template-created alert; failure cleanup removed the exact harness tree and left the repository marker-free. This hardening does not claim to eliminate a theoretical ancestor-symlink TOCTOU.
* 2026-08-31: A third PR #145 review follow-up handles an unignored repository-local `TMPDIR` without treating the runner-owned tree as an unexpected worktree change. The runner captures the baseline before creating the harness, removes the exact harness only after validating its path, ownership, and device/inode identity, and then compares Git porcelain in memory. Eight deterministic harness tests pass. The repository-local regression proves that the owned tree and its `oom` marker are removed, an unrelated unexpected repository file still fails the guard and remains present, and a sibling beside the owned tree is preserved. No cleanup scope or ownership guard was broadened; no additional full engine profile was run because this ordering-only host boundary is covered directly and the unchanged application profiles retain the previously recorded E2E evidence and T53 browser limitations.
* 2026-08-31: Canonical dependency sync, Ruff formatting/linting, and `ty` checks passed. The default Pytest profile completed with 2,114 passed, 44 deselected, 4 failed, and 32 setup errors; the full profile completed with 2,121 passed, 41 failed, and 32 setup errors. T52 coverage passed in both. The failures are unrelated environment/baseline gaps: missing PostgreSQL and S3 test environment variables, unavailable document engines and font evidence in the full profile, and an existing release test that expects the old boto3 lower bound while the exact base already declares the newer bound.
* 2026-08-31: PR #145 merged to `main` as `a78a93edd5504c6a67ffce0c9e8b24481527dc27`. Exact-main CI run 33396700639 attempt 1 passed light and distributed E2E, but standalone failed in the fresh browser session-expiry phase with `page.waitForTimeout: Page crashed`, so the gate failed. The bounded failed-job attempt 2 on the same SHA passed standalone E2E and the gate, and the overall run completed successfully. The attempt-1 failure occurred during the 61-second browser expiry wait before restart assertions, matches the browser resource-phase symptoms owned by T53, and produced no T52 worktree, harness-validation, or marker-cleanup diagnostic. With both profiles verified through their complete restart and recovery workflows on the merged implementation and the exact-main retry green, all T52 acceptance criteria are complete.

## Coordination

* Status: Done; synchronize Linear to Done only after this completion record is verified on `main`.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
