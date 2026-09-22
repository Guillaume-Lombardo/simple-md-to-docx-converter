---
ticket: T83
linear_id: G1L-582
linear_url: https://linear.app/g1lom/issue/G1L-582/t83-preserve-slide-structure-in-powerpoint-to-markdown-exports
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T83 - Preserve slide structure in PowerPoint to Markdown exports

## Objective

Complete the remaining reverse-conversion scope from T82: provide opt-in slide-oriented Markdown/Marp output for edited PowerPoint presentations in 2md. Ordinary anydoc extraction remains available in 0.7.0; original source recovery in a 2pptx bundle is not edited-slide extraction.

## Acceptance criteria

* Qualify pinned anydoc slide boundary, presenter note and image capabilities before implementation.
* Offer explicit slide separators, optional presenter notes/images and Markdown or Marp output without silently dropping slide content.
* If required, implement only a narrowly bounded non-executing OOXML presentation reader inside the existing isolated reverse attempt, retaining archive/XML/image limits, no network, authorization, cancellation, recovery and retention.
* Persist options and include them in idempotency for both SQLite and PostgreSQL profiles; expose matching HTTP/CLI/browser contracts and regenerated bindings.
* Add unit/security, actual-boundary integration, both-storage and final rootless image E2E coverage, including critical failure/authorization behavior.
* Document source recovery versus extraction, fidelity limitations and safe image packaging; no OCR, arbitrary CSS, executable formats or full visual round-trip promise.

## Dependencies

T82 forward PowerPoint baseline; T69-T72 reverse-conversion isolation and lifecycle.

## Progress

2026-09-22: PR #259 at `d14055e1d6c974ac9b2b3d007888a470e93947cd` exposed an outdated
final-image API smoke assertion: the validator required seven capability keys and rejected the new T83 `extraction` field. The user authorized the minimal correction and resumed lifecycle.
The validator now requires the exact eight-key schema and structured extraction object, including
actual boolean defaults; all earlier authentication, availability, headers, determinism and execution
guards remain unchanged. Independent review approved the two-file correction. All 75 container-asset
tests and global Ruff/ty pass. The correction affects the host-side test harness, not application
or image build inputs. The existing-qualified-image API smoke passed in 26.2 seconds against exact backend configuration
`e7c53afc1d239e06bd2f907e7dd5b372b931cda2f06702fcaf4b53ad8c36861b`; both legacy-unavailable
and configured standalone workflows passed, temporary resources were removed, and tested harness
hashes remained unchanged. Evidence bundle: `t83-pr259-api-smoke-20260922T082722Z`. New exact-head
CI remains pending.

2026-09-22: A new local final-image candidate was built and qualified from clean source
`4888cd067e848c59162d801c2399be99b7f81969`. Preflight and 13 focused boundary tests passed; the
28-mutant campaign, the three-image build, and the uncompressed reverse-runtime
OCI export also passed. The initial final-image run stopped before workflow execution because Podman
could not unpack a layer after the host filesystem filled (`ENOSPC`). Its rollback/pull failure is
retained as diagnostic evidence. The corrected attempt used only the verified disk prerequisite and
artifact offload; the runner and source were unchanged. It completed standalone and distributed
workflows, 21 resource measurements, and all three CI-mode supply-chain scans. Independent ASTRA
review approved the recorded checksums and source/runner guards. The scans reported zero Critical
findings; High counts were 133 (backend), 39 (frontend), and 134 (reverse attempt). The embedded
reverse Cargo evidence also reported zero Critical and High findings. The reverse runtime identity is
manifest `sha256:518e9f2cd71e999ed5e719ccdf3d554f1505d97ce925830eff684ec8aeb322f8`, derived from the
uncompressed OCI export. The compressed scan archive manifest
`sha256:4977529c5c86a3ab01c1718732303f265056893196cc037495d938db182d1106` is scan evidence only,
not the runtime identity. The qualified images are now deployed to the Docker-box test instance and
public HTTPS endpoint `https://markweave.g1lom.xyz`. Backend `e7c53afc`, frontend `da476b26`, and
reverse runtime manifest `518e9f2c` match the qualified candidate. Four services are healthy; the
broker is active, schema 19 is present, and no job was active before cutover. A fresh independently
verified host-copy backup is `69bd3ba43e9bc1ed77509f4d06f7d383058fe4591a41c9adb8f329478a0f4ed7`, and the old
virtual environment/configuration remains available for rollback. An authenticated structured Slides
smoke job `000fc13e-fda2-417e-b338-521e0a584324` succeeded with a 1,572-byte result and SHA-256
`317f4e6c13f6c6bfec17af623bfa3670472d3b6c802f8138ac418eb556acf528`; it verified notes, images,
and warnings. Both reverse READY metrics were 1, the session logged out, the final smoke helper exited
0, and router/public readiness passed. The original cutover script exited 1 after backend/frontend
update when fixture transfer failed at `docker cp`/tmpfs; it is retained as a failed receipt. After
fixture staging through tar stdin, the approved final helper passed without changing the candidate. An
earlier backup-creation UID failure stopped safely and restored the old healthy service before the
independently approved default-image-user correction. This deployment is distinct from pending PR,
`main` integration, and public-release decisions; T83 remains In Progress. See
[final-image evidence](../docs/evidence/t83-structured-pptx-final-images.md).

2026-09-22: A subsequent local validation snapshot at clean `984da696d0ad2c8b0810414037a19bd65f967bb4`
merged the reviewed normal T73 result and was independently approved. Its only application
delta from the prior `912926ec46b25f135c93377d0cae885d3cd29fe7` snapshot is the reviewed 12-line CLI deadline fix; PPTX behavior is unchanged, while the CLI correction changes backend image inputs. `uv sync --all-groups`, global Ruff and `ty`, 255 focused CLI/CI policy tests (18.14s),
and the clean release-install test (15.93s) passed. The actual 28-mutant campaign also killed all
28 mutants with all six failure statuses zero in 55s, guarded by clean exact-head status before and
after. Receipts are retained under operator bundle ID `t83-mutation-all-20260922T003120Z`, report
SHA-256 `bbe887d9d520c0dd83f87bb9152bc5c7183173998e04657f38889d0d697103e0`. This was local
validation only: no fresh image build, deployment, publication, or current-main claim. Exact PR
and main CI remain pending; a full main `582886e5c87799b6de19a5fc3c0369916055f7a0` artifact rerun awaits authorization.

2026-09-22: Terminal final-image attempt 3 completed both standalone and distributed profiles with
exit 0 against one matched three-image candidate set. The application images were built from
`e3fb99be5763bb8fc6f100be43140e61e221f82d`; the clean harness head was
`b186d3ac3718a71205196b24e0a60f72455ee8ee`, and its application build-input diff was empty. The
structured PPTX API, installed CLI, and browser Marp paths passed with exact option and package
inspection, alongside the existing reverse lifecycle, eight-family corpus, authorization/scanner,
cancellation/expiry/unavailable, worker/broker crash, outage, and recovery checks. Resource
measurements contain 21 cases and the existing T69 concurrency comparison without an approved
production numerical threshold. CI-mode scans completed with zero Critical and High counts 133
backend, 39 frontend, and 134 reverse; complete bundle evidence is retained. See
[final-image evidence](../docs/evidence/t83-structured-pptx-final-images.md). This does not claim
publication, deployment, `main` integration, or completion; engine-marked CI, exact-head/main
checks, and T73 stacked integration remain separate boundaries.

2026-09-20: Split from T82 during user-authorized 0.7.0 publication preparation so unfinished reverse options remain explicitly tracked. No reverse parser or option implementation is claimed.

2026-09-21: Started implementation after pinned anydoc qualification confirmed flattened slide boundaries, presenter notes emitted as ordinary block quotes, and embedded image asset retention. The user selected notes and images enabled by default for opt-in structured Markdown/Marp extraction, with warnings and explicit placeholders for unsupported meaningful content. Existing anydoc extraction remains the default. Work stays inside the isolated reverse attempt with bounded OOXML reading, configurable reverse-specific limits, deterministic packaging and frozen lifecycle/idempotency options. T50, T73, T85 and T86 remain separate; no release/version change is included.

2026-09-21: Completed the isolated reader/core slice. Presentation ordering, blank/hidden slides, grouped text, basic lists/tables, separate optional notes, normalized image positions and deterministic packages are implemented with explicit unsupported-content warnings. Ordinary anydoc behavior remains the default; structured output carries an optional content-free extractor identifier. Independent reader review findings for filled/empty drawings, Markdown fences, action-only links and ragged-table expansion were fixed and re-reviewed. Local core validation passes 177 focused tests, scoped Ruff formatting/lint, global ty and diff checks; targeted new-core coverage was 97% before the final additive security cases. Qualification and configurable-bound evidence is in docs/pptx-extraction-qualification.md. Full canonical suites, both-profile workflow integration and final rootless image E2E remain unclaimed by this core slice. T83 stays In Progress; no version/release/publication change was made.

2026-09-21: Integrated the complete local feature candidate: immutable extraction options are persisted and included in idempotency, carried by the existing broker/attempt protocols, enforced in result traceability, and exposed consistently through HTTP, CLI and capability-derived browser controls. OpenAPI and generated bindings are synchronized. Independent core and lifecycle/client reviews approved the final code with no remaining actionable findings. Verification includes 177 focused core tests; a broad focused backend run of 710 tests plus separate 103-test protocol/service and 57-test persistence follow-ups; 43 CLI tests and 26 targeted frontend tests; reviewers independently passed 176 Python tests, 26 frontend tests and 16 native browser-helper tests. These runs overlap and are not an aggregate total. Canonical Ruff formatting/lint, global ty, contract/binding and diff checks passed. PostgreSQL execution, canonical Python suites and their overall/changed-line coverage gates remain pending; a deliberately partial normal-coverage run did not satisfy the global gate. Final rootless two-profile E2E remains mandatory and pending the T73 harness foundation after T50, without an exception or waiver. No completion, publication, release or final-image acceptance is claimed.

2026-09-21: The first canonical engine-excluded run at d0f9537 was interrupted after a frozen-clock supervisor wait stalled. Its nonqualifying terminal result was 357 passed, 3 failed and 1 teardown resource-warning error; PostgreSQL had not yet run and partial coverage did not meet the global gate. All three failures traced to Unix-client preflight assuming every content-limit field was an integer, rejecting the new unset optional PPTX limits. The correction reconstructs the exact limits dataclass to retain strict validation while accepting optional None fields. Added real Unix/mTLS staging regressions for anydoc, slides and Marp and bounded the test-only supervisor wait. The correction passes 29 focused tests; all four selected original process/teardown cases pass without suppressing warnings. Independent re-review approved the four-file code/test correction and passed 13 selected tests; root also approved. Canonical validation is restarting on the separate correction commit; full coverage, PostgreSQL and final rootless acceptance are not yet claimed.

2026-09-21: The complete canonical engine-excluded run at fee1069 finished with 4,541 passed, 1 failed, 56 deselected and 11 warnings in 1,815.43 seconds. Actual PostgreSQL and RustFS integrations executed successfully. Combined coverage was 94.94%, branch coverage 91.14% (6,613/7,256), and changed-line coverage 96.99% (612/631). The sole failure was migration 19 using DROP COLUMN during downgrade, unsupported by deployed SQLite 3.34. The minimal correction uses Alembic table-copy downgrade on SQLite and native ALTER TABLE on PostgreSQL. Populated-job roundtrip regressions preserve rows, defaults, foreign keys, indexes and checks in both profiles; the PostgreSQL case uses the existing per-test isolated schema and all engines dispose on failure. The original failure and both profile regressions pass (3 tests); independent review also passed all three and approved the corrected test isolation. Canonical formatting, lint, type and diff checks pass. The failed run is retained as diagnostic evidence, not a passing suite claim. A separate correction commit precedes another canonical run; final rootless two-profile acceptance remains mandatory and pending T73, with no waiver.

2026-09-21: Canonical engine-excluded validation passed on dc2ee475acac0d556090ce4262a631babc3ac034: 4,544 passed, 56 deselected and 11 warnings in 1,835.82 seconds, exit 0. Actual PostgreSQL and RustFS integrations executed, including the isolated populated-row migration regression. Combined coverage is 94.95%; application branch coverage is 91.17% (6,615/7,256), and changed-line coverage is 97.00% (614/633) against the feature base. The 11 warnings are dependency deprecations and existing SESSION_IDLE_SECONDS warnings, not resource leaks. The exact commit remained stable throughout the run and the worktree was clean. Canonical format/lint/type and generated contract/binding checks also pass. Both separately committed corrections were independently reviewed. Full external-engine and final rootless two-profile workflow qualification remain pending; the latter requires the T73 harness foundation and is not waived. T83 remains In Progress, with no publication, release or main-branch completion claimed.

2026-09-21: Added independently reviewed edited-PPTX E2E preparation at 671e5c55e76d24e17821ffff496513069ee62049. Structured Slides/Marp assertions cover modified slide text/order, notes, exact normalized image pixels and unsupported-content warnings; legacy anydoc defaults and option/owner errors remain covered. Six focused tests and local native-parser execution passed; final-image execution is still pending. Normal merge 15a41ae2d0ae352eae6d2615688a80dd41b847d3 integrates verified main 741ea85 and T85 history filters, regenerating both combined API binding trees. Validation passed: 155 focused Python tests, 63 frontend tests, binding freshness, type and static checks. Independent merge review approved both parents and the combined generated contracts with no findings. No publication or completion is claimed; T73 integration and both-profile structured workflow execution remain required.

2026-09-21: The integrated candidate passed the canonical engine-excluded Python suite at clean `0980498b8a5ac6db575c09c99fe0814794a518a9`: 4,674 passed, 56 deselected and 11 warnings in 1,869.88 seconds. Actual PostgreSQL, RustFS, broker transport and populated migration regressions executed. Overall coverage is 94.96%, branch coverage 91.17% (6,617/7,258), and changed application coverage 97.07% (629/648). Global Ruff formatting/lint and ty pass. The full frontend check passes 232 tests with 90.06% branch coverage after adding the missing unavailable-extraction-mode assertion; the preceding 89.99% coverage failure is retained separately.

2026-09-21: Final client coverage review found that the structured final-image driver exercised the API but did not select structured options through the installed CLI or browser. Extended the existing drivers with one edited-PPTX path each: CLI Slides with default notes/images and invalid-option/no-job proof, and browser Marp with live controls, exact submitted options and accepted-job download inspection. Both reuse the edited fixture and existing harness; the API retains the complete two-mode result/asset checks. Independent static review approved the corrected navigation, bounded inspector and exact job binding. All 48 focused driver/harness tests, global static checks, and the exact browser ZIP inspector against a native Marp result pass. These are test-only additions after the canonical run; actual final-image execution in both profiles and exact-head/main CI remain pending. No release or completion is claimed.

2026-09-21: The first complete final-image standalone run at `e3fb99b` passed structured API and installed CLI PowerPoint scenarios, then stopped in the new browser test because Chromium/Playwright returned no raw multipart upload buffer despite HTTP 202. This is retained as a failed run, not a product acceptance pass. The minimal test-only correction removes the unsupported raw-body observer while retaining live controls, authoritative accepted options, exact job binding, and actual Marp package inspection. No product code, timeout, image or acceptance criterion changed; both complete profile workflows still need to finish successfully.

2026-09-21: The second standalone final-image run used unchanged `e3fb99b` images with the `f1316a3` harness. The real structured API, installed CLI and browser Marp workflows passed, including authoritative accepted options and the exact downloaded package. The complete run then failed in the older synthetic workspace presentation fixture, which omitted the now-required extraction capabilities and reverse-job options. The fixture is updated to the current contract, with an assertion that DOCX does not expose PowerPoint extraction controls. Both failed runs and their artifacts are retained; complete two-profile qualification is still pending. No application code or image changed.

2026-09-22: Deployed the qualified candidate to the existing docker-box test instance after verifying a complete database/object backup. Backend/frontend/native broker and the exact reverse runtime digest are matched; migration 19, SQLite integrity, all four service health checks, public HTTPS readiness, authenticated structured Slides package validation and both reverse READY metrics pass. A Podman compressed-import identity mismatch was diagnosed and corrected without relaxing checks; its known-never-started creation was recovered through the existing adapter and normal reconciliation under independent review, without direct inventory/data edits. Both synthetic jobs ultimately succeeded. This remains a test candidate at package version 0.7.1, with no public release, PR merge, or Done claim.

* 2026-09-22: The authorized capabilities smoke correction passed the container job in CI
  35705172160. That run then failed standalone E2E and its dependent gate; every other job passed.
  A separately authorized, independently reviewed inventory snapshot correction addresses a real
  concurrent-read race without weakening manifest authentication or tamper rejection. T73 records
  its 45-test evidence and negative control. Seven guides now distinguish historical qualified
  candidates from the pending new-source qualification and T87's authorized 0.7.2 publication;
  118 documentation/policy tests passed. New exact-head and main verification remain required.
* 2026-09-22: Head `078569c2ed534c3355a5671cad297bf6d6b267bb` passed every hosted check
  in CI `35709082811`, including both complete E2E profiles. The actual changed-domain mutation
  report selected and killed 14 mutants, with all failure statuses zero. The local canonical suite
  reported 4,694 passed, two failed and 56 engine-marked deselected. Journals establish that the
  host disk-cleanup service pruned the unused workspace fixture image at 09:14:49 UTC, before
  both systemd workspace tests reported that exact image missing at CREATE. The user authorized
  fixture-owned stopped retention containers and their exact cleanup, independent review and
  resumed validation. No system cleanup policy change is authorized or needed. Completion and
  publication remain pending successful validation of this harness correction.
* 2026-09-22: The fixture correction protects base, process and workspace images with
  never-started containers, without broker-managed labels, implicit pulls or anonymous volumes.
  The focused process integration module passed all 31 cases using the retained external reverse
  image; all temporary retainers were removed and that external image was preserved. The initial
  fixture assertion used Podman's internal `configured` name rather than its actual inspect value
  `created`; that setup failure and the corrected passing run are retained separately. Independent
  review requested a narrow cleanup improvement for partial creation or malformed command output
  before final validation. No application code or system policy changed in this correction.
* 2026-09-22: Final fixture validation passed all 33 process integration cases, including real
  container creation followed by injected malformed output or timeout. Both regressions verify
  removal of the exact generated retainer and preservation of the external image. No retainers
  remained after the run; source and test-diff guards passed. Global Ruff/ty and documentation
  checks pass. The corrected canonical suite and new exact-head CI remain required before merge.
* 2026-09-22: Main CI `35747016968` and protected release run `35747017469` passed at source
  `84e35fed521c61d34823d18767f47eb87253d4eb`. Public schema-2 receipts and anonymous manifests
  verify the matched backend, frontend, and reverse-attempt images, including the structured-PPTX
  release source. Independent full-artifact verification passed, followed by successful Docker,
  Podman, and insecure-Podman public quickstarts from clean adoption source
  `aa35c96a9d05b56fee5cd683272b2ff2242469df`. T83 remains In Progress until adoption and deployment
  verification finish. Deployment preparation stopped when `uv` parent-project discovery changed
  the host-native broker package from 0.7.1 to 0.6.2; image identities were unchanged, and the remote
  repair has not yet run.
* 2026-09-22: Adoption PR #262 CI `35755639720` passed all 12 jobs and merged as
  `96e3940de134ca3ccf3ed1d8749dc955472bc719`; its Git tree matches tested head
  `6b5a182864ccb4ceb43b01770113d23fa610edc1`. Docker-box deployment of the public 0.7.3 set and
  native package completed. Authenticated structured Slides smoke job
  `0b56308d-abb1-4062-8311-5fcdd30466fa` succeeded; its inspected 1,572-byte package has SHA-256
  `317f4e6c13f6c6bfec17af623bfa3670472d3b6c802f8138ac418eb556acf528`. Public and reverse
  readiness, migration 19, backup, rollback, logout, and zero-active-job checks passed. Automatic
  main CI `35759392518` was active when recorded and is monitored separately. The feature, release,
  adoption, and deployed structured-PPTX evidence satisfy the remaining gates; T83 is Done.
