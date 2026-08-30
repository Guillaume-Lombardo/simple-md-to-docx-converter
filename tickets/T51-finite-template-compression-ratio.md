---
ticket: T51
linear_id: G1L-434
linear_url: https://linear.app/g1lom/issue/G1L-434/t51-reject-non-finite-template-compression-ratios
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T51 - Reject non-finite template compression ratios

## Objective

Reject non-finite template compression ratios at the typed configuration boundary instead of
accepting them and failing later while assembling the template validator.

## Acceptance criteria

* `template_max_compression_ratio` accepts only finite values greater than or equal to one.
* Positive infinity and NaN are rejected by `Settings` through the existing sanitized validation
  behavior.
* Existing finite values keep their behavior.
* Targeted configuration tests and every applicable canonical quality gate pass.

## Dependencies

* T06
* T18

## Implementation boundary

* Own `src/markweave/config.py`, the mirrored configuration test, and this ticket only.
* Do not change template validation, runtime engines, storage, HTTP APIs, dependencies, or the
  product specification.

## Progress

* 2026-08-30: Created after reproducing that `Settings` accepts positive infinity before the
  template domain rejects it during composition.
* 2026-08-30: Added finite-number validation plus positive-infinity and NaN regressions. The
  targeted configuration suite passes 105 tests; Ruff, formatting, `ty`, and all 23 JavaScript
  tests are green. Both canonical Python suites were executed and retain more than 95% coverage,
  but this macOS environment lacks the required PostgreSQL/RustFS variables and approved Linux
  document-engine/container boundaries; those unrelated integration gates remain for GitHub CI.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
