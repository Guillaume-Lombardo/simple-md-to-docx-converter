---
ticket: T101
linear_id: G1L-620
linear_url: https://linear.app/g1lom/issue/G1L-620/t101-separate-the-normative-specification-from-delivery-history
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T101 - Separate the normative specification from delivery history

## Objective

Make the product specification shorter and easier to review by moving historical delivery narrative, completed-ticket choreography and release history out of the normative document.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to change, item 6).

## Acceptance criteria

- Keep sections 1-12 and 14 as the normative contract; move completed delivery ordering notes, dated priority paragraphs and release narratives to a non-normative `docs/delivery-history.md`.
- Keep the section 13 ticket table, or replace it with a link to the ticket mirrors plus a short table of open tickets, as the owner prefers.
- Keep the development-workflow wording in section 1 tool-neutral.
- No normative requirement is lost: produce a reviewed list of every moved paragraph and its destination.
- Documentation tests and links pass.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- T96 edits the same specification sections; this ticket waits for T96 so only one ticket owns the specification at a time.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-620.
