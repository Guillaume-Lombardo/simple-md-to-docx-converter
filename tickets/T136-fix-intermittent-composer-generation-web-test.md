---
ticket: T136
linear_id: G1L-689
linear_url: https://linear.app/g1lom/issue/G1L-689/t136-fix-the-intermittent-composer-generation-workspace-web-test
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T136 - Fix the intermittent composer generation workspace web test

## Objective

Remove an intermittent failure in the web unit test `web/tests/composer-generation-workspace-coverage.test.tsx > a rejected template version requires an explicit refreshed choice before retry`. It fails with `AssertionError: expected "vi.fn()" to be called with arguments: [ { …(10) }, …(3) ]` and makes `CI / light` fail at random.

Observed on 2026-10-12 in PR #285 (T98) runs 38268478604 (head e5543f7) and 38280813643 (head 926817a). Neither change touched `web/`, the web lockfile or `package.json`. The test passed 5 of 5 times when run alone and 3 of 3 times in full `vitest run --coverage` runs locally, and the CI reruns passed.

## Acceptance criteria

- Identify the root cause with evidence. Candidates include an unawaited state update, a timer or microtask ordering issue, a mock shared across tests, or a timing difference introduced by coverage instrumentation.
- Fix the test or the component without weakening what it asserts: the rejected template version must still require an explicit refreshed choice before retry.
- Show stability by running the test file repeatedly, for example 50 times, with and without coverage, and by running the full web suite several times.

## Dependencies

- None.

## Blocking points

- None known.

## Progress

- 2026-10-12: Created from intermittent `CI / light` failures during T98.
- 2026-10-21: Started on branch `fix/T136-composer-generation-web-test` from `origin/main` `95bc188`.
- 2026-10-21: Root cause: a component race, not only a test race.
  - `templateLoading` started as `false` and became `true` only in a `queueMicrotask` inside the
    catalog effect. In the commit that first renders the generation controls, before that effect
    runs, the "Style template" select (holding only "Pandoc default") and "Generate DOCX" were
    enabled although `api.templates` had not been called. A `MutationObserver` probe recorded
    `{"disabled":false,"options":1,"templatesCalls":0}` on that commit.
  - The failing assertion is the first `toHaveBeenCalledWith` (the message names
    `toHaveBeenCalledWith`, not `toHaveBeenLastCalledWith`). When the test continues inside that
    window, `fireEvent.change` to the template id has no matching option, so the select reports
    "Pandoc default", the choice is remembered as `pandoc`, and generation starts with
    `template_id: null`.
  - Whether the test continues inside that window depends on event-loop ordering. After
    `findByRole`, Testing Library drains one `setTimeout(0)`; React schedules passive effects with
    `setImmediate` under Node and jsdom. Node does not order the two when the timer is already due,
    which load and coverage instrumentation make more likely. This explains why the failure
    appeared only on busy CI runners and never in isolated local runs.
  - The same window let a user generate with the Pandoc default before a remembered or resolved
    template was applied.
- 2026-10-21: Fix.
  - `web/src/composer/workspace.tsx` derives `templateLoading` from the catalog request that last
    settled versus the current one (owner, draft, output, retry). Style selection and generation are
    therefore disabled from the first render until the matching catalog settles. The `setState` in
    a microtask and the manual `setTemplateLoading(true)` on output change are removed.
  - New regression test `style selection and generation stay disabled until the template catalog
    settles` observes every commit with a `MutationObserver` while the catalog is pending. It fails
    on `main` (`['style', 'generate']` enabled before the catalog) and passes with the fix.
  - Three `composer-workspace.test.tsx` tests clicked "Generate DOCX" as soon as it appeared, inside
    the same window. They now wait for the button to be enabled, as the other Composer generation
    tests already do; their assertions are unchanged.
- 2026-10-21: Stability evidence (Node 24.19.0, 4 vCPU).
  - The test file passed 50 of 50 runs without coverage and 50 of 50 with coverage (thresholds
    disabled for single-file runs only).
  - The full web suite passed 3 of 3 runs with `vitest run --coverage` and its 90% thresholds:
    845 tests, branches 90.09%.
  - `prettier`, `eslint --max-warnings=0` and `tsc --noEmit` pass on the changed files.
  - Reproduction under load (4 CPU hogs): on `main`, the original test failed 4 of 60 runs (1 of
    30 without coverage, 3 of 30 with coverage), each with the CI message `expected "vi.fn()" to be
    called with arguments: [ { …(10) }, …(3) ]`. With the fix, under the same load, it failed 0
    of 60 runs.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-689.
