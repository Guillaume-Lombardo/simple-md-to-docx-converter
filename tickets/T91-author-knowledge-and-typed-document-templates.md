---
ticket: T91
linear_id: G1L-590
linear_url: https://linear.app/g1lom/issue/G1L-590/t91-add-structured-author-knowledge-and-typed-document-templates
status: Done
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
- 2026-09-26: Ready PR #270 at `9a6c6288acb8ffbbe53c2f5c21ca315000f10a00` had terminal
  failure in hosted CI run `36255154728`: 15 jobs passed and five failed. Both 30-minute Python
  shards passed, but combined application branch coverage was 89.73% (8,296/9,246); the 90% gate
  needs 26 more covered branches. Read-only analysis of the three authentic coverage artifacts
  found that engine-independent real DOCX corpus tests were omitted from the light selection;
  the unchanged changed-line gate would pass at 93.37% (2,449/2,623). The light job passed 688
  of 689 web tests; the 100-author pagination case took 5,027 ms against its five-second limit.
  Standalone image E2E failed on a Generate DOCX locator that also matched Regenerate DOCX.
  Distributed image E2E exposed a question snapshot race after a model step completed.
  The owner authorized four scoped corrections: a case-only ten-second test limit, an exact
  Generate DOCX locator, `light_coverage` selection for engine-free DOCX corpus cases only, and
  a bounded question refresh on model-step completion with a regression test. Implementation,
  independent review, full hosted rerun, both complete image profiles, main verification, and
  matched docker-box deployment/rollback remain open. T91 remains In Progress.
- 2026-09-26: All four authorized corrections are committed and independently reviewed. The
  browser E2E locator now matches Generate DOCX exactly (`65d43fa`); the engine-free DOCX corpus
  is selected for light coverage (`238c816`), with 48 of 49 cases passing and the LibreOffice
  case excluded as intended. A local aggregate projection reaches 90.14% application branch
  coverage and 94.85% changed-line coverage; this is not a hosted CI result. The Composer fix
  (`ceadfe8`) reconciles questions and proposals after a model step settles and guards late
  success/error responses across draft changes; its focused 49 web tests and the full 694-test
  web suite pass, with 90.04% web branch coverage. The author pagination test has a scoped
  ten-second limit. Hosted CI and both complete final rootless-image profiles still require
  reruns on the exact final head. Main verification and matched docker-box deployment/rollback
  remain pending. T91 stays In Progress; no completion is claimed.
- 2026-10-11: Completed and verified on `main`. Every acceptance criterion is evidenced:
  - The permissioned author directory with audited share/revoke, typed DOCX fill templates with
    schema and value validation, reviewable fill plans and missing-value questions, frozen
    approved values and exact template versions for regeneration without a model call, and
    API/CLI/UI operations were delivered by PR #270
    (`5b3385cfb7dd9a4631a917242e1711fdb83177fc`) from head `72961a37`, after independent
    backend and client reviews and review of the four authorized corrections. PR CI run
    36259030259 and main CI run 36261485425 passed, including Python coverage, `container`,
    and both final rootless-image E2E profiles.
  - Owner-authorized docker-box test-instance deployment completed on 2026-10-11 with matched
    images built from `main` `00a98ec1dc49cdc82f383dbf0e2cd05724a8d37b`, which contains T89,
    T90, T91, and later fixes: backend
    `sha256:96fef52ab721dc91cdd5d73c7c92b6f112e7f2fdbc31a13f30149f9b96825f87`, frontend/router
    `sha256:cc1584ca7ee0d70960061aa1c81fb5e73b0ed75bb1a5dc62c8341bf5850e9820`, and
    reverse-attempt
    `sha256:eb2542a813f1aa161bafd043fbbb7f6c427261df25863928eac5a69d91979208`.
    Qualification: a passing local standalone final-image E2E run on those exact bytes, hosted
    CI run 38062285755 passing `e2e-standalone`, `e2e-distributed`, and `container` on
    code-identical `b764b99`, and scheduled CI run 38130312124 passing the same jobs on
    `00a98ec`. No local distributed E2E run used the exact deployed bytes.
  - Before cutover, a verified recovery set was taken with the previous image and the stopped
    data volume was copied cold and byte-verified (schema `20260923_24`, SQLite `quick_check`
    ok, one account). The cutover migrated to `20260924_26`; account identity, password hash,
    and Composer key were unchanged. The public HTTPS smoke passed: readiness, API version,
    login with the existing administrator password, account listing, Composer
    policy/drafts/capabilities/authors/fill-templates APIs, the `/convert`, `/templates`,
    `/composer`, `/composer/authors`, `/composer/fill-templates`, `/composer/connections`, and
    `/admin/llm` pages, template upload and scan, DOCX, PDF, PPTX-to-Markdown through the mTLS
    broker, and logout.
  - Rollback rehearsal: the exact previous T90 image set started against an isolated copy of
    the cold pre-cutover volume was healthy at schema 24 with `quick_check` ok; public
    readiness, login, listing, and logout passed and row counts were unchanged. The instance was
    then rolled forward, re-verified, and passed the full public smoke with zero failures. A
    post-cutover backup taken with the new image was verified and restored into an isolated
    directory. Final state: four services healthy, broker active, public `/health/ready`
    returning 200, and running image IDs independently confirmed.
  - Limitations and follow-ups: restoring the pre-cutover schema-24 recovery set with the new
    image is refused, a known defect tracked by T128 / G1L-681 (PR #290), so a real rollback
    uses the cold copy, as the docker-box deployment notes document. The broker certificates
    expire on 2026-10-19 at 14:36 UTC and were not renewed; renewal is an owner follow-up.
    Interactive browser login was not exercised on the live instance; API login was.
    Intermittent final-image E2E scanner-outage timeouts are tracked by T130 / G1L-683.

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
