---
ticket: T136
linear_id: G1L-689
linear_url: https://linear.app/g1lom/issue/G1L-689/t136-fix-the-intermittent-composer-generation-workspace-web-test
status: Backlog
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

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-689.
