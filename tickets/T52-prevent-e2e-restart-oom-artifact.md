---
ticket: T52
linear_id: G1L-459
linear_url: https://linear.app/g1lom/issue/G1L-459/t52-prevent-final-image-e2e-restart-oom-artifact
status: Backlog
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

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
