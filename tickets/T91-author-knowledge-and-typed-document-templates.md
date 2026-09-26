---
ticket: T91
linear_id: G1L-590
linear_url: https://linear.app/g1lom/issue/G1L-590/t91-add-structured-author-knowledge-and-typed-document-templates
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T91 - Add structured author knowledge and typed document templates

## Objective

Add permission-aware structured author knowledge and genuine typed document filling while
retaining the current Pandoc style-reference templates.

## Acceptance criteria

- Provide a personal or explicitly shared structured author directory, selection among authorized
  entries, source provenance, audited share/revoke, and permission checks before model transmission
  and on resumed work.
- Add typed fields, constraints, and bounded repeatable sections for filling templates; distinguish
  them from current Pandoc style references, retain existing template compatibility and visibility,
  and reject malformed schemas or invalid values without publication.
- Ask for missing author, finding, decision, and other required data and resolve ambiguities through
  reviewable proposals; later model output must not silently overwrite manual edits.
- Freeze approved values and exact template versions for regeneration without an LLM call.
- Expose documented API, CLI, and UI operations, audit/configuration controls, and primary/failure
  integration and final-rootless-image E2E tests in both profiles, including sharing/revocation,
  malformed schemas, concurrency, and reproducibility.
- Complete independent review, required checks, and qualified docker-box candidate deployment.

## Dependencies

T90.

## Progress

- 2026-09-22: Created in Linear and mirrored before implementation. No delivery is claimed.
- 2026-09-24: Started implementation from verified T90 merge `57521ed62eb19558b945e1109934bcc6bb967ac8` on `feat/T91-author-templates`. T89 and T90 are merged prerequisites. Linear G1L-590 could not be fetched or updated because its connection expired; the user explicitly authorized proceeding and synchronization remains due when access returns. No T91 delivery is claimed yet.
- 2026-09-24: Private author grants, scanned and versioned typed DOCX templates, reviewed fill plans, frozen publication/regeneration, and API/CLI/Admin/Composer flows are implemented in the local branch. Independent read-only review closed its permission, provenance, audit, and OOXML findings. Standalone and distributed final-image Composer E2E, web checks (684 tests; 90.03% branch coverage), real corpus-derived LibreOffice DOCX rendering, and the canonical default Python suite (5,349 passed; 60 engine-marked tests deselected; 90.14% application branch coverage; 94.75% changed application-line coverage) pass. Host document engines remain unavailable, so the engine-marked host suite is unverified. Publication, merge, qualified candidate deployment, verification on main, and deferred Linear synchronization remain due; no completion is claimed.

- 2026-09-26: Independent follow-up review found that frozen regeneration needed to recheck author
  grants before publication. The correction is committed at `90bc89b34b875465c0dcf0ffbf981156bea1fd26`;
  focused verification passed 11 SQLite and two PostgreSQL tests. Separate browser corrections
  passed 688 web tests with 90.10% branch coverage plus formatting, lint, generated bindings,
  types, and structure checks; independent rereview and commit are pending. The canonical default
  Python suite on the corrected backend stopped at a T91 HTTP test that expected regeneration
  after an author version update; the new permission check returned a precondition failure.
  Focused rerun reproduced that assertion, while 11 revision/retention tests passed independently.
  The backend correction and a complete rerun remain pending. Exact final-head
  coverage, hosted CI, both complete image profiles, and matched docker-box deployment/rollback
  remain open. T91 remains In Progress.
- 2026-09-26: The independently reviewed follow-up `de1f11af45be53cb093a59240d1bffaeac37612e`
  preserves frozen approved regeneration after author content edits while checking current access;
  the original HTTP regression now passes. Its focused verification passed 13 SQLite/HTTP and
  three PostgreSQL tests, Ruff, and `ty`. Browser corrections are committed at
  `e3e7aba1c9d0f15a51138d8776aa4e70cc3c5b76` and passed 689 web tests with 90.02% branch
  coverage plus the full web check;
  independent rereview passed. The canonical default Python suite is being rerun on this final
  source. Both complete final-image profiles, hosted checks, verified main merge, and matched
  docker-box deployment/rollback remain required. T91 remains In Progress.

- 2026-09-26: The final-head default Python suite passed 5,356 tests with 90.16% application
  branch coverage and 94.85% changed Python line coverage; the web suite passed 689 tests with
  90.02% branch coverage. PR #270 publishes `52a2f662188e4901fd6a8ca19a2787a69a353c7c`.
  A complete standalone final-image run passed the Composer browser scenarios, then failed at
  stale provider event counts: 12 successful chat calls were observed where the harness expected
  10. The distributed full-image run has not started. The two successful chat calls come from the
  typed scenario's connection test and model step; the observed two provider outages and two
  unauthorized responses matched the harness. T91 remains In Progress.
- 2026-09-26: Corrected the two full-image harness expectations to 12 successful chat calls after
  the Composer browser phase and 17 cumulatively after resilience and recovery. Bash syntax, Ruff
  formatting/lint, and `ty` pass. The focused harness/quickstart tests passed 94 tests, but that
  invocation failed the repository-wide coverage threshold, which requires the full suite. Both
  complete final-image profiles, hosted checks, verified main merge, and matched docker-box
  deployment/rollback remain open. No complete profile pass is claimed yet.
- 2026-09-26: Hosted CI run `36233804329` reached the 25-minute limit in both complementary
  Python shards; one had passed 2,392 tests at 24:52 before coverage upload was canceled, and
  the other had reached 99%. Increased only their reviewed limit to 30 minutes, updated the
  canonical workflow digest, and added a validator regression that rejects 25 and accepts 30.
  All 197 CI validation tests, the validator entry point, Ruff formatting/lint, and `ty` pass.
  Hosted checks at the updated head, complete final-image profiles, main verification, and
  matched docker-box deployment/rollback remain open. T91 stays In Progress.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-590.

- 2026-09-26: Linear access returned. G1L-590 now records In Progress and the committed local
  candidate at `bc42c8b9c296198b2075cc7e9f8daa12039b1d5b`, with the original High priority,
  T90 dependency, and full acceptance criteria intact. Repeated `uv sync --all-groups`, Ruff
  format check and lint, and `ty` pass on that exact clean source. An independent review then
  found a regeneration permission edge case; the correction and focused tests are recorded above.
  The earlier Python, web, and final-image Composer results apply to the original candidate and
  must be reconsidered after the correction. The retained local final-image artifacts show passing
  Composer scenarios in standalone and distributed profiles; they do not establish a complete
  unattended profile run at the final source head. Publication, hosted checks, main verification, and
  matched docker-box deployment/rollback remain open; no completion is claimed.
