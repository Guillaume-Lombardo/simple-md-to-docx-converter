---
ticket: T53
linear_id: G1L-460
linear_url: https://linear.app/g1lom/issue/G1L-460/t53-isolate-final-image-browser-e2e-resource-phases
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T53 - Isolate final-image browser E2E resource phases

## Objective

Harden the final-image browser E2E resource phases so Chromium failures retain enough bounded evidence to distinguish cgroup memory pressure, shared-memory exhaustion, process exhaustion, and navigation-observation defects.

## Acceptance criteria

* Close or discard completed traces and browser contexts that are no longer needed before allocating the fresh session-expiry context.
* Start trace coverage for the fresh expiry context before its login and retain it on failure.
* Keep screenshots and Playwright traces failure-only under the existing private artifact directory and 30-day CI retention, using only repository-owned synthetic documents and ephemeral test-only identities; mask password and file inputs in screenshots, and never introduce operator credentials, user documents, or host paths into those artifacts.
* Retain a separate bounded resource-diagnostic payload for cgroup memory current/peak/events, PID count, `/dev/shm` usage, and process names through an explicit field allowlist that excludes command arguments, credentials, document content, and host paths.
* Keep the existing URL and authentication assertions; do not hide failures with broader skips or unconditional retries.
* Add deterministic policy tests for phase cleanup and retained evidence.
* Validate standalone and distributed final-image workflows and the exact hosted CI revision.

## Dependencies

* T34

## Implementation boundary

* Own browser-phase cleanup and sanitized failure diagnostics in the final-image harness.
* Do not change application memory limits, authentication behavior, template CLI behavior, or production runtime configuration.
* Serialize implementation with T52 because both may touch the restart harness.

## Progress

* 2026-08-30: Created from T34 PR #127 CI diagnosis. Exact-head run 33325930671 failed standalone browser three times after the new template CLI workload: two `waitForURL` timeouts despite successful `/convert` navigation and DOM/network-idle, and one Chromium target crash. Same-base T42 and T45 standalone jobs passed. T34 owns the immediate phase-order fix; T53 owns systemic resource evidence and cleanup.
* 2026-08-30: Review clarified the failure-artifact boundary. Existing screenshots and Playwright traces remain failure-only, private, synthetic-fixture artifacts with masked screenshot inputs and 30-day CI retention; the new resource-diagnostic payload is separately allowlisted and must contain no command arguments, credentials, document content, or host paths.
* 2026-08-31: T52 merged through PR #145 as `a78a93edd5504c6a67ffce0c9e8b24481527dc27`, completing serialization on the shared final-image harness. Exact-main CI run 33396700639 attempt 1 added another fresh session-expiry symptom: standalone passed its security, service, and CLI phases, then Chromium reported `page.waitForTimeout: Page crashed` at the 61-second expiry wait before restart assertions. Distributed E2E passed in attempt 1, and the bounded failed-job attempt 2 passed standalone and the gate on the unchanged SHA. This intermittent result remains T53 browser resource-phase evidence; T53 stays Backlog until its own implementation begins.
* 2026-08-31: Implementation started from exact clean `main` at `0a9bd11a25f6d0ea5d5a2a68f0c329701e5b4b0e` after verifying T34/G1L-410 and T52/G1L-459 are Done. The bounded plan owns only the final-image browser harness: close completed traces and contexts before the fresh expiry phase, trace that phase before login and retain failure-only private artifacts, add explicitly allowlisted resource diagnostics without arguments, credentials, document content, or host paths, and add deterministic policy coverage before validating both final-image profiles and exact hosted CI. Application memory limits, authentication behavior, template CLI behavior, production runtime configuration, retries, skips, URLs, and authorization assertions remain unchanged.
* 2026-08-31: Implemented deterministic browser-phase release and failure evidence. Completed traces are discarded before their contexts close; the fresh expiry context starts tracing before page creation and login. Failure handling now writes a separate owner-only, fixed-schema diagnostic containing bounded cgroup memory counters/events, PID count, `/dev/shm` capacity/use, and sanitized process-name counts, with a runner fallback before artifact copying. Policy tests cover cleanup order, failure-only collection order, trace timing, schema allowlisting, private permissions, malformed or unavailable probes, and bounded process-name cardinality. Focused checks, Web tests, Ruff, and `ty` pass. Canonical suites exceed 95% coverage but cannot complete the real container, PostgreSQL, S3, or document-engine cases because rootless Podman reports an invalid pause-process state and the host lacks the required engines; the unrelated release integration assertion also retains the obsolete `boto3>=1.40` expectation while current project metadata requires `boto3>=1.43.82`. Standalone/distributed final-image runs and exact hosted-CI revision validation remain pending; no repository revision was published from this worktree.
* 2026-09-01: Re-ran the focused artifact-policy suite (29 passed), the full Web suite (29 passed), Bash syntax validation, Ruff format/lint, and `ty` (all passed). The default Python suite again passed through the non-container portion then encountered repeated PostgreSQL container errors and stalled during the unavailable rootless-Podman path; both invocations were stopped cleanly rather than left running. The standalone and distributed final-image workflows, full Python suite, and exact hosted-CI revision remain pending an environment with functioning rootless Podman and the required engines. The change remains In Progress until it is verified on `main`.
* 2026-09-01: Independent review required an atomic failure-diagnostic publication and a valid runner fallback for stopped/OOM-killed application containers. Diagnostics now use a same-directory exclusive temporary file, `0600` chmod, atomic rename, and temporary-file cleanup. The runner validates the full fixed schema rather than file existence, repairs missing or malformed payloads, and creates a bounded host-side fallback containing only safe container state, exit code, and OOM evidence when the application cannot run the collector. Deterministic regressions cover interrupted replacement cleanup, schema rejection for unsafe extra fields, stopped-container payloads, fallback ordering, copied ownership, and private mode. Focused and full Web suites (31 passed), Bash syntax validation, Ruff format/lint, and `ty` pass.
* 2026-09-01: Re-review corrected the fallback command contract and stopped-container probe boundary. The `--output` CLI option now denotes the exact final diagnostic file, so the runner writes and validates the expected path without suppressing an unverified fallback failure. CLI fallback explicitly emits null cgroup and shared-memory fields with no process names; it retains only allowlisted container state, exit code, and OOM evidence. The strict validator now rejects non-string process names. An integration-style CLI regression invokes the same command shape for missing and malformed output and verifies a valid `0600` final file, no temporary remnants, repaired payload, and null stopped-container probes. Focused and full Web suites (32 passed), Bash syntax validation, Ruff format/lint, and `ty` pass.
* 2026-09-01: Third review restored normal in-container collection. The no-argument collector retains cgroup, PID, shared-memory, and process-name probes; only the explicit `--host-fallback` runner path suppresses host probes for a stopped application container. Controlled collector tests verify default probe collection and fallback zero-probe behavior, while the actual CLI fallback regression continues to validate the final path, privacy mode, atomic cleanup, and safe stopped-container payload. Focused and full Web suites (32 passed), Bash syntax validation, Ruff format/lint, and `ty` pass.
* 2026-09-01: PR review hardened failure cleanup. Missing `podman inspect` output is guarded and normalized, and a missing browser-artifact directory no longer interrupts teardown. Completed browser phases now settle every trace discard and context close; a regression covers a failed trace discard. Focused Web suite (33 passed), Bash syntax validation, Ruff format/lint, and `ty` pass.
* 2026-09-01: Follow-up review ensures a failed diagnostic collector records failure without preventing container, volume, harness, and worktree cleanup. Browser-phase cleanup now attempts every trace and context, then propagates a context-close failure to prevent the next phase from allocating. Regression coverage includes the guarded collector call and a rejected context close after every context is attempted. Full Web suite (34 passed), Bash syntax validation, Ruff format/lint, and `ty` pass.

## Coordination

* Status: In Progress.
* One worker owns the final-image harness files at a time.
* Serialize implementation with related T52; T52 is not a Linear blocker for this ticket.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
