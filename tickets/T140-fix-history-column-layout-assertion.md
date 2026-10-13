---
ticket: T140
linear_id: G1L-693
linear_url: https://linear.app/g1lom/issue/G1L-693/t140-fix-the-intermittent-history-column-layout-assertion-in-the
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T140 - Fix the intermittent history-column layout assertion in the browser workspace UI E2E

## Problem

`tests/e2e/browser-workspace-ui.test.mjs`, in "all workspaces show selected files, named history,
primary downloads and collapsed slide options", fails intermittently in final-image E2E:

`Completion must not move the history column`, with `#recent-heading` y differing by exactly
36 px.

Observed twice:

- in the T130 investigation (standalone): 892.1875 against 928.1875;
- in hosted run 38306066586, job `CI / e2e-distributed-2` at T135 head `62d72fc`: 928.1875
  against 892.1875.

The direction of the shift varies, so the `before` measurement is probably taken while a 36 px
element is appearing or disappearing, for example a status notice, a progress row or a banner,
and not after the layout has settled.

## Scope

- Identify the 36 px element and the state transition that causes the race, using a trace or
  screenshots around the `before` and `after` measurements.
- If the product really moves the history column on completion, fix the layout and keep the
  assertion.
- If only the measurement races, wait for a deterministic settled state before measuring. The
  assertion must not be weakened: completion must still not move the column.

## Acceptance criteria

- The root cause is documented.
- The scenario passes in at least three consecutive hosted runs of both profiles.
- A deliberately injected layout shift on completion still fails the test.

## Dependencies

- None.

## Root cause

At the 480 px viewport the conversion workspace stacks the form above the status and history
column, and the page is scrolled to the bottom when the test clicks the history entry. The
template search starts 250 ms after the workspace becomes ready and shows "Searching templates…"
in an `aria-live` region of the form. While that region is empty its `space-y-3` margin collapses,
so showing the indicator adds its 24 px line plus the 12 px margin: 36 px above the history
column. At the bottom of the page that growth moves `#recent-heading` by 36 px in the viewport.
Whether the debounced search is visible during the `before` or the `after` measurement depends on
runner speed, which explains both directions of the shift. Completion itself does not move the
column.

Evidence, from a local production build of `web/` and the test under Chrome CPU throttling
(rate 3) with delayed IndexedDB reads:

- a per-frame layout trace showed the document height change from 1,515 to 1,551 px exactly when
  `searching` became true, moving the heading from 892.1875 to 928.1875;
- 4 of 12 throttled runs measured a different heading position before and after completion; one
  failed the assertion.

## Progress

- 2026-10-13: Mirrored from Linear and started on branch `fix/T140-history-column-layout-race`
  from `origin/main` `961d174`.
- 2026-10-13: The search indicator region now reserves its line (`min-h-6`), like the status and
  action regions next to the history column already do. The browser test now also holds a named
  template search open and asserts that the visible indicator does not move the history column.
  - Without the layout fix, that new assertion fails deterministically with 928.1875 against
    892.1875, the hosted values.
  - With the fix, an injected extra line in the completed status region fails
    `Completion must not move the history column`.
  - With the fix, 36 throttled diagnostic runs (CPU rates 1, 3, 4 and 6; IndexedDB delays from
    0 to 120 ms) and 11 unthrottled runs of the test against a local production build passed.
- 2026-10-13: Opened PR #307. Images built with `scripts.container.ci_images build` from the branch
  passed final-image E2E shard 2 (`browser-workspaces` included) locally in both profiles. The
  first hosted run of the PR passed every check, including `e2e-distributed-2` and
  `e2e-standalone-2`. Two more consecutive hosted runs in both profiles are still required.
