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
* On browser failure, retain bounded and sanitized evidence for cgroup memory current/peak/events, PID count, `/dev/shm` usage, and process names without command arguments, credentials, document content, or host paths.
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

## Coordination

* Status: Backlog.
* One worker owns the final-image harness files at a time.
* Serialize implementation with related T52; T52 is not a Linear blocker for this ticket.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
