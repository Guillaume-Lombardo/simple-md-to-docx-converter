---
ticket: T98
linear_id: G1L-630
linear_url: https://linear.app/g1lom/issue/G1L-630/t98-make-the-local-python-test-suite-fast-enough-for-iteration
status: Backlog
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

- Coverage merging across workers and the custom `scripts.ci.pytest_branch_coverage` plugin must be proven equivalent before CI adopts parallelism.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-630.
