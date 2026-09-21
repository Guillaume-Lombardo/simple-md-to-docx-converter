---
ticket: T83
linear_id: G1L-582
linear_url: https://linear.app/g1lom/issue/G1L-582/t83-preserve-slide-structure-in-powerpoint-to-markdown-exports
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T83 - Preserve slide structure in PowerPoint to Markdown exports

## Objective

Complete the remaining reverse-conversion scope from T82: provide opt-in slide-oriented Markdown/Marp output for edited PowerPoint presentations in 2md. Ordinary anydoc extraction remains available in 0.7.0; original source recovery in a 2pptx bundle is not edited-slide extraction.

## Acceptance criteria

* Qualify pinned anydoc slide boundary, presenter note and image capabilities before implementation.
* Offer explicit slide separators, optional presenter notes/images and Markdown or Marp output without silently dropping slide content.
* If required, implement only a narrowly bounded non-executing OOXML presentation reader inside the existing isolated reverse attempt, retaining archive/XML/image limits, no network, authorization, cancellation, recovery and retention.
* Persist options and include them in idempotency for both SQLite and PostgreSQL profiles; expose matching HTTP/CLI/browser contracts and regenerated bindings.
* Add unit/security, actual-boundary integration, both-storage and final rootless image E2E coverage, including critical failure/authorization behavior.
* Document source recovery versus extraction, fidelity limitations and safe image packaging; no OCR, arbitrary CSS, executable formats or full visual round-trip promise.

## Dependencies

T82 forward PowerPoint baseline; T69-T72 reverse-conversion isolation and lifecycle.

## Progress

2026-09-20: Split from T82 during user-authorized 0.7.0 publication preparation so unfinished reverse options remain explicitly tracked. No reverse parser or option implementation is claimed.

2026-09-21: Started implementation after pinned anydoc qualification confirmed flattened slide boundaries, presenter notes emitted as ordinary block quotes, and embedded image asset retention. The user selected notes and images enabled by default for opt-in structured Markdown/Marp extraction, with warnings and explicit placeholders for unsupported meaningful content. Existing anydoc extraction remains the default. Work stays inside the isolated reverse attempt with bounded OOXML reading, configurable reverse-specific limits, deterministic packaging and frozen lifecycle/idempotency options. T50, T73, T85 and T86 remain separate; no release/version change is included.

2026-09-21: Completed the isolated reader/core slice. Presentation ordering, blank/hidden slides, grouped text, basic lists/tables, separate optional notes, normalized image positions and deterministic packages are implemented with explicit unsupported-content warnings. Ordinary anydoc behavior remains the default; structured output carries an optional content-free extractor identifier. Independent reader review findings for filled/empty drawings, Markdown fences, action-only links and ragged-table expansion were fixed and re-reviewed. Local core validation passes 177 focused tests, scoped Ruff formatting/lint, global ty and diff checks; targeted new-core coverage was 97% before the final additive security cases. Qualification and configurable-bound evidence is in docs/pptx-extraction-qualification.md. Full canonical suites, both-profile workflow integration and final rootless image E2E remain unclaimed by this core slice. T83 stays In Progress; no version/release/publication change was made.

2026-09-21: Integrated the complete local feature candidate: immutable extraction options are persisted and included in idempotency, carried by the existing broker/attempt protocols, enforced in result traceability, and exposed consistently through HTTP, CLI and capability-derived browser controls. OpenAPI and generated bindings are synchronized. Independent core and lifecycle/client reviews approved the final code with no remaining actionable findings. Verification includes 177 focused core tests; a broad focused backend run of 710 tests plus separate 103-test protocol/service and 57-test persistence follow-ups; 43 CLI tests and 26 targeted frontend tests; reviewers independently passed 176 Python tests, 26 frontend tests and 16 native browser-helper tests. These runs overlap and are not an aggregate total. Canonical Ruff formatting/lint, global ty, contract/binding and diff checks passed. PostgreSQL execution, canonical Python suites and their overall/changed-line coverage gates remain pending; a deliberately partial normal-coverage run did not satisfy the global gate. Final rootless two-profile E2E remains mandatory and pending the T73 harness foundation after T50, without an exception or waiver. No completion, publication, release or final-image acceptance is claimed.

2026-09-21: The first canonical engine-excluded run at d0f9537 was interrupted after a frozen-clock supervisor wait stalled. Its nonqualifying terminal result was 357 passed, 3 failed and 1 teardown resource-warning error; PostgreSQL had not yet run and partial coverage did not meet the global gate. All three failures traced to Unix-client preflight assuming every content-limit field was an integer, rejecting the new unset optional PPTX limits. The correction reconstructs the exact limits dataclass to retain strict validation while accepting optional None fields. Added real Unix/mTLS staging regressions for anydoc, slides and Marp and bounded the test-only supervisor wait. The correction passes 29 focused tests; all four selected original process/teardown cases pass without suppressing warnings. Independent re-review approved the four-file code/test correction and passed 13 selected tests; root also approved. Canonical validation is restarting on the separate correction commit; full coverage, PostgreSQL and final rootless acceptance are not yet claimed.
