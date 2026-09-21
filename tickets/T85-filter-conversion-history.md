---
ticket: T85
linear_id: G1L-584
linear_url: https://linear.app/g1lom/issue/G1L-584/t85-filter-conversion-history-in-the-api
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T85 - Filter conversion history in the API

## Objective

Move the existing recent-history filtering into the owner-scoped API so 2docx and 2pptx do not scan all conversion pages to obtain ten matching non-expired jobs.

## Acceptance criteria

* Add optional validated server-side output-family and expired-state filtering to GET /api/v1/conversions, preserving the current unfiltered default contract.
* Apply filters before pagination and compute the matching total in SQLite and PostgreSQL; preserve stable ordering and owner isolation without loading the complete history in application memory.
* Update the browser to request its relevant non-expired history in one bounded paginated request. Preserve empty/loading/error and cancellation behavior.
* Regenerate OpenAPI and frontend bindings and document filter semantics; existing CLI behavior remains compatible.
* Cover both database profiles, pagination across mixed outputs and expired jobs, invalid filters, two users and administrator authorization, and final rootless browser/API E2E regression.
* Run applicable canonical checks and independent review; retain the existing coverage thresholds.

## Dependencies

* T12, T13, T45, T62, T82 (delivered baselines).

## Implementation boundary

Own conversion history query, HTTP/OpenAPI/client contract, recent-history UI and focused tests. Do not add speculative text/date searches, alter reverse-conversion history or refactor unrelated persistence.

## Progress

* 2026-09-21: Created from the user priority table, priority order 3 after T50 and T73. Existing UI filters expired jobs and PowerPoint output locally while fetching successive pages; no duplicate ticket exists.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
