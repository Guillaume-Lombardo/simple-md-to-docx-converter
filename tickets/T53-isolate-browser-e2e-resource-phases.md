---
ticket: T53
linear_id: G1L-460
linear_url: https://linear.app/g1lom/issue/G1L-460/t53-isolate-final-image-browser-e2e-resource-phases
status: Backlog
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

## Coordination

* Status: Backlog.
* One worker owns the final-image harness files at a time.
* Serialize implementation with related T52; T52 is not a Linear blocker for this ticket.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
