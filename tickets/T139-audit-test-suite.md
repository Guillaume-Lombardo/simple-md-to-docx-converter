---
ticket: T139
linear_id: G1L-692
linear_url: https://linear.app/g1lom/issue/G1L-692/t139-audit-the-test-suite-for-redundant-obsolete-or-low-value-tests
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T139 - Audit the test suite for redundant, obsolete or low-value tests

## Goal

The owner asked on 2026-10-12 to check whether every test is still useful and which ones could be
removed. This is part of the CI and local test speed effort (T98, T130, T135).

## Scope

1. **Audit.** Inventory the Python, web and E2E suites and find candidates in these categories:
   - duplicates or near-duplicates, including tests that are better expressed as a parametrized
     case;
   - tests whose coverage is a strict subset of other tests and whose assertions add nothing;
   - tests that cannot fail, or that have no meaningful assertion;
   - tests of removed or superseded behaviour;
   - brittle source-text or layout pins that duplicate checks `scripts/ci/validate_ci.py` already
     enforces;
   - overlapping slow integration tests and E2E scenarios.
2. **Owner decision.** Present the candidates with evidence, the expected time saving and the
   effect on coverage. The owner decides what to remove, merge or keep.
3. **Implementation.** Apply only the approved changes, in a separate pull request.

## Acceptance criteria

- The audit report lists every candidate with its evidence and a recommendation: delete, merge,
  parametrize or keep.
- No removal lowers application branch coverage below 90%, and none drops coverage of a security
  area, acceptance criterion, integration boundary or final-image E2E requirement from `AGENTS.md`
  or the product specification.
- The approved changes pass the canonical checks, and their before-and-after timings are recorded.

## Dependencies

- None. Coordinate with T135, T137 and T138 on files in flight.

## Audit summary (2026-10-12, read-only, main `9306cab`)

- **Inventory.** 6,144 Python tests, 844 vitest tests, and final-image E2E made of 20 Node browser
  files executed 33 times across phases plus 10 Python workflow drivers. The 4,081 unit tests take
  89 s of worker time in total, so deleting unit tests cannot save meaningful time.
- **D1.** `tests/test_t22_maintenance.py::test_mutation_workflow_is_isolated_bounded_and_read_only`
  repeats assertions the `mutation.yml` policy in `scripts/ci/validate_ci.py` already enforces and
  covers no application arc.
- **D2.** The retired T67 tooling tests (8 functions, 12 cases, in `tests/test_pnpm_workspace.py`,
  and the 10 cases of `tests/integration/test_benchmark_timeout.py`) test scripts no workflow has
  run since T80. They cover no application arc and go only together with those scripts.
- **P1 to P5.** Identical bodies or identical arcs and assertions in `test_reference_corpus`,
  `test_typed_templates`, `test_validation` (a security area: keep every input),
  `test_connections` and `test_egress_guards`, and the Mermaid, changelog and protocol pairs.
- **K1.** The default canonical command fails on `main`; T138 owns it.
- **K2.** 8 cases had no category marker and ran in no CI job, including an upload-security test.
- **K3.** 54 integration cases run in no CI job.
- **K4.** Several `tests/test_ci_validation.py` tests only check that some error is raised. The
  canonical digest pin of `ci.yml` always raises one, so these tests cannot detect a lost rule.
- **K5.** E2E time sinks: reverse lease and expiry waits, and service exercise.
- **C1 to C3.** `storage-standalone` (202 tests) and `functional` (38) are contained in the light
  shards, and `storage-distributed` (78) is identical to the always-run "Python distributed
  coverage" job: about 5 runner-minutes per full run, no wall-time effect.

## Owner decision (2026-10-12)

Approved: K2, K4, P1 to P5 (keeping every input), D1, D2 (tests deleted together with the retired
scripts they test), and C1 to C3. Not approved in this scope: K3 and K5.

## Progress

- 2026-10-13: Mirrored from Linear and started on branch `chore/T139-test-suite-cleanup` from
  `origin/main` `0e0f7f3`.
