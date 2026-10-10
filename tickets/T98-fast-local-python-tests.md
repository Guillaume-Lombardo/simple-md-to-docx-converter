---
ticket: T98
linear_id: G1L-630
linear_url: https://linear.app/g1lom/issue/G1L-630/t98-make-the-local-python-test-suite-fast-enough-for-iteration
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T98 - Make the local Python test suite fast enough for iteration

## Objective

Reduce the wall time of the unit and canonical local suites so a contributor can run them routinely; a plain `pytest tests/unit` run did not finish within 15 minutes during the 2026-10-09 analysis.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: follow-up note on test duration).

## Acceptance criteria

- Measure and record the baseline with `--durations` for unit and canonical runs.
- Add parallel execution (for example pytest-xdist, through `uv`) that is compatible with the branch-coverage plugin, disposable PostgreSQL/RustFS services and mutation testing; keep results deterministic.
- Move or mark genuinely slow tests with `slow` without excluding them from CI.
- Document a fast developer loop and keep the canonical commands authoritative.
- Target: unit suite under 3 minutes and canonical engine-excluding suite under 10 minutes on the reference developer host; record the achieved figures.
- Coverage thresholds (90% branch, 90% changed lines) and CI gates stay unchanged.

## Dependencies

- T97 - Mark Podman-dependent tests and repair the default local test command

## Blocking points

- Coverage merging across workers and the custom `scripts.ci.pytest_branch_coverage` plugin must be proven equivalent before CI adopts parallelism. Recommended approach (owner-approved 2026-10-09): pytest-xdist with pytest-cov's built-in xdist support, after a short spike on the custom plugin.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

- 2026-10-10: Started a narrowly scoped CI reliability correction during T96 PR #278
  qualification. Attempt 1 passed all 2,701 shard tests in 29:59 before cancellation prevented
  coverage upload; attempt 2 reached 99% without failures before the same 30-minute cutoff.
  Raise only the reviewed Python shard bound to 40 minutes with matching validator and regression
  checks. Preserve test selection, two complementary shards, same-attempt coverage, 90% gates,
  permissions and all other job bounds. The broader measurement, parallelism and developer-loop
  criteria remain pending behind T97; this correction does not complete T98.

- 2026-10-10: The bounded CI correction passed exact-head CI 38008305925 and was verified
  on main in PR #278 squash `628fd2f310a456c6e2b94b25c071f321cbef1d6b`. Both complementary
  Python shards and distributed coverage were combined from the same attempt; 5,106 tests passed
  with 90.14% application branch coverage and unchanged 90% gates. The reviewed 40-minute bound
  remains finite; no selection, permission or other job-limit change was introduced. This is
  partial delivery only: duration measurement, parallelism, developer-loop documentation and
  reference-host speed targets remain pending behind T97. T98 remains In Progress.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-630.
