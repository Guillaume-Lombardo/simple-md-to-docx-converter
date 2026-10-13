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
- 2026-10-13: Implemented the approved scope, one commit per category.
  - **K2.** The four unmarked functions (8 cases) are now `unit` tests. The new
    `tests/category_marker_guard.py`, registered through `addopts`, reports every test under
    `tests/` without a category marker as a collection error before any marker or shard
    deselection, in serial and xdist runs. No other unmarked test exists on `main`.
  - **K4.** With every validator rule except the canonical digest disabled, 34 cases in five
    tests still passed: inert or indirect commands, neutralizing fields, the light shard and
    distributed coverage contracts, and trusted cache writes. Each case now asserts its rule
    error. Disabling each of the 16 targeted rules makes its cases fail. Only the two tests that
    target the digest rule itself still pass on the digest alone.
  - **P1 to P5.** Duplicates are parametrized with every input kept and intent-revealing ids;
    collected cases per file and parameter values are unchanged. The protocol pair in
    `tests/unit/broker/test_protocol.py` is left to T137, which owns that directory. P6 is not
    approved (see the review correction below).
  - **D1.** Every assertion of the deleted test is rejected by the `mutation.yml` policy; the
    exact 30-minute timeout and the absence of `github.token` are enforced by its canonical
    digest only.
  - **D2.** No workflow, package script, specification section or live document runs the T67
    scripts. They are removed with their tests, and the evidence record points to Git history.
    `tests/integration/test_benchmark_timeout.py` called the bounded runner in-process, which
    made the pytest process a child subreaper. That is why the eight
    `test_local_test_services_interruption.py` cases (K1, T138) failed in the canonical run on
    `main` and why the run took about 16 minutes: on `main`, running the two modules together
    fails the eight cases, and the interruption module alone passes.
  - **C1 to C3.** Collected node ids on `main` `0e0f7f3`: the 38 `functional` and 202
    `storage-standalone` cases are all in the light shards (5,843 cases, whose two shards are
    exactly complementary), and the 78 `storage-distributed` cases equal the always-run
    distributed coverage selection. Neither domain had a service, variable or marker that those
    jobs lack. The domains are removed from the registry, selector, heavy matrix (which drops its
    storage services) and validator, whose canonical digest is updated. The marker guard now
    requires `unit` or `light_coverage` under `tests/functional`, `tests/integration/auth` and
    `tests/integration/sqlite`.
  - **Measurements** (canonical default command, same machine, low load):

    | | `main` `0e0f7f3` | branch |
    | --- | --- | --- |
    | Collected tests | 6,231 | 6,232 |
    | Selected by the canonical command | 6,116 | 6,117 |
    | Result | 8 failed (K1), 6,108 passed | 6,117 passed |
    | Wall time | 976 s | 478 s |
    | Application branch coverage | 90.47% (8,591/9,496) | 90.46% (8,590/9,496) |

    No `src/markweave` arc covered in every `main` run is missing from the branch runs. Five
    arcs vary between runs of unchanged tests in both trees (job submission, source activation
    and model-step expiry races).
- 2026-10-13: Independent review round 1 (review 5497692323 on PR #303) required two changes.
  - **Scope correction.** The owner approved P1 to P5 only, not P6, and including P6 came from the
    implementation brief. The repository-guard and mutable-action release-policy tests are
    restored exactly as on `main`; they already asserted their specific rule errors, so K4 does not
    apply. The merged light shard and distributed coverage test remains part of K4, which
    rewrote those cases to assert specific rules.
  - **Marker guard under xdist.** A failed collection report stopped serial sessions but not
    `pytest-xdist` workers, which still ran the remaining tests. On a violation the guard now also
    drops every collected item, so no test body runs: serial sessions exit with status 2 and xdist
    sessions with status 1. A subprocess regression test runs `-n 2` with and without `-m unit`
    and checks the node-id diagnostic, the exit status and that no test body ran. It fails when
    the abort is removed.
- 2026-10-13: Merged `origin/main` after T137 (`016bbe7`) and T138 (`c5c0a89`).
  - T138 had changed `scripts/javascript/run_bounded_benchmark_command.py` to restore the
    subreaper attribute. The merge keeps the T139 deletion. The T138 autouse guard
    `keep_child_subreaper_state` in `tests/conftest.py` and its harness test do not depend on the
    deleted supervisor and keep working.
  - The marker guard rejected two new T137 tests in `tests/unit/broker/test_cgroup_events_polling.py`
    that had no category marker. They use a fake clock and are now marked `unit`. Every other new
    T137 and T138 test already had a marker.
