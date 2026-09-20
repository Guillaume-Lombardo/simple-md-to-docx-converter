---
ticket: T82
linear_id: G1L-581
linear_url: https://linear.app/g1lom/issue/G1L-581/t82-add-editable-powerpoint-generation-and-markdown-round-trips
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T82 - Add editable PowerPoint generation and Markdown round trips

## Objective

Implement the approved editable PowerPoint workflow: rename the forward tab to 2docx; add independent 2pptx for Markdown, a documented Marp subset, and ZIP assets; manage typed PowerPoint templates; export re-editable packages; add slide-oriented Markdown/Marp options to 2md.

## Acceptance criteria

- Omitted presentation templates always use Pandoc's native default reference, with no catalog requirement. Test real generation without any templates, plus selected immutable versions and wrong-type rejection.
- Accept ordinary Markdown and explicit slide Markdown/Marp; configurable heading level, pre-generation slide outline and unsupported-directive/overflow warnings; never silently summarize or discard content.
- Generate editable PPTX using pinned Pandoc; support bounded local images, existing Mermaid processing, tables, columns and presenter notes.
- Add immutable docx/pptx template types, safe OOXML/layout/font validation, versioning, ownership, replacement/restore, typed search and downloadable starter template.
- Persist presentation options in the existing durable queue, include them in idempotency, preserve cancellation/recovery/retention and both storage profiles.
- Offer PPTX or portable re-editable ZIP containing source, assets and generation settings; distinguish original source recovery from extracting subsequently edited slides.
- Track slide-oriented 2md Markdown/Marp export, presenter note and image options separately in T83; do not claim those options in the 0.7.0 forward release.
- Preserve independent source selection and navigation, authoritative same-origin API contracts and HTTP CLI coverage; regenerate OpenAPI/bindings.
- Hide expired jobs from every Recent conversions list, both on initial load and when a job update reports expiration; retain historical API/CLI access.
- Add unit, security, actual-engine integration, both-storage and final-image E2E coverage; run applicable canonical checks and report missing evidence.

## Scope exclusions

Faithful rasterized Marp export, arbitrary CSS/HTML or executable Quarto, arbitrary placeholder templating, source embedded in PPTX, OCR and automatic summarization are deferred.

## Dependencies

T81 navigation baseline; T07/T08/T09/T10 document pipeline; T12/T13/T15/T16/T17 persistence, jobs and templates; T69/T70/T71/T72 reverse conversion.

## Progress

2026-09-20: User approved implementation and explicitly required template-free PPTX generation. Implementation started; no Git publication or deployment requested for this new feature.

2026-09-20: Recent conversion lists filter expired entries on load and on job updates.
Four controller regressions cover mixed history, all-expired history, and expiration
of a selected job. The frontend check passes (211 tests, nine structure checks,
90.40% branch coverage, formatting, lint, TypeScript and binding consistency).
The final-image browser expiration scenario was updated; its Docker execution remains
pending with the broader PowerPoint implementation.

2026-09-20: The 2pptx forward workflow is implemented: native Pandoc fallback,
Markdown/Marp planning, typed immutable PowerPoint templates, source bundles,
durable options and idempotency, migration 18, HTTP contracts, CLI conversion,
and the independent browser workspace. Real Pandoc tests verify editable slides,
notes, selected themes and ZIP image assets. SQLite and PostgreSQL repository
contracts pass. The browser workflow passes against the rootless candidate backend
with real Pandoc/LibreOffice (isolated synthetic scanner) and production Next.js:
default generation/download, starter reference, template creation and selected generation.
Frontend checks pass with 225 tests and 90.12% branch coverage. Ruff and ty pass.
The broad Python run exposed stale HTTP contract snapshots, now regenerated and
verified (54 adapter tests); the full coverage run is in progress. Docker-box image
builds started; deployment is pending SSH-agent authorization. No publication or
merge has occurred. Slide-specific reverse extraction options remain unfinished;
T82 remains In Progress. See docs/powerpoint.md for supported syntax and limitations.

2026-09-20 final forward-workflow validation: canonical filtered Pytest passes
(4,364 tests, 50 engine-marked tests deselected, 94.63% total coverage and 90.83%
branch coverage). An additional instrumented 116-test run covers real Pandoc,
ZIP images, unsafe PPTX templates, CLI options and both SQL migration boundaries.
Combined coverage: 91.24% branches, 96.67% changed executable lines, including
untracked source files. Ruff format/lint and ty pass. The unfiltered host engine
suite was not run because host LibreOffice/Mermaid are unavailable; real LibreOffice
activation was covered inside the rootless candidate. Local test services are stopped.
Docker-box is NOT updated: SSH signing is refused or times out. The user was asked
to unlock/approve 1Password. Remote source/build were started earlier, but completion
cannot currently be inspected. Backup/deploy helpers are prepared locally. No push
or merge. Slide-specific reverse extraction remains outstanding.

2026-09-20 deployment completed after user unlocked SSH: database backed up with
SQLite integrity verification, then migration 18 and the matched candidate backend/
frontend activated on docker-box. All four services are healthy and the broker is
active. Public HTTPS readiness, pages and PowerPoint OpenAPI routes pass. The actual
rootless deployed backend generates editable PPTX without any reference template.
Deployed HTTPS assets show 2pptx and the tight version position in Chromium using
synthetic API responses; authenticated public conversion remains for user acceptance.
Backend digest: fc800467ca6b8d820902b3e7541a2c801e518a4e9aac318373e16aecb6478d18.
Frontend digest: 4397af2e16cdc89840312957165ac12c4d6ec7ebedb39081a787e899a5d86063.
Remote DEPLOYMENT.md records evidence and schema-aware rollback constraints.
No Git publication or merge; ticket remains In Progress.

2026-09-20 acceptance UI corrections: all three workspaces use a visually hidden
native file input plus a state-driven Choose/Change file control and selected name,
so drag/drop cannot leave a contradictory native No file selected label. Download
buttons use primary-button like Start conversion. Forward responses now expose the
already-persisted source_filename; recent lists display document names with UUID
tooltips, including reverse history. Slide structure uses native details/summary
and starts closed. Frontend checks pass (225 tests, 90.09% branch coverage); 59
focused HTTP/functional tests pass. Rootless UI acceptance test covers actual served
assets with synthetic API responses across all three routes, picker/drop selection,
reloaded named history, tooltip, download styling and disclosure state.

2026-09-20 UI corrections deployed to docker-box; four services healthy, broker
active, public readiness and source_filename API verified. Chromium acceptance passes
on deployed assets with synthetic API responses across the three workspaces (picker,
drop, reloaded history, tooltip, download styling, collapsed slide settings).
Backend dbabd7b439fc92db5985ebbb888ab064ae64c82925f2b9241a9ff86cb2d97c54;
frontend 7b918471e77f47545e1aa612faa50b5e8b3ea0b9ca582972bea2577e5cfca019.
No schema change or new authenticated public conversion. Previous image pair retained
in compose.t82.pre-ui-fixes.yaml. No publication or merge.

2026-09-20 selection hierarchy refinement: the guidance/selected-file text is now
large, centered and wrap-safe, with a small Choose/Change file control underneath
in all three workspaces. A succeeded forward job displays only its full-width
Download result action in place of the progress bar. Running jobs, even at 100%,
do not expose downloads. Frontend checks pass (225 tests, 90.09% branch coverage).
Production-build Chromium UI checks with synthetic API responses pass, including
vertical ordering/font hierarchy and running-at-100% to succeeded transition.
Frontend-only test image build in progress; backend and database are unchanged.

2026-09-20 hierarchy update deployed: frontend image
sha256:d1c78dfb3e9bf3abfc9012cb51ead22f5f5cc706f121e7d24d69d3250bb75856.
Backend/schema unchanged, four services healthy. The same Chromium UI acceptance
passes against publicly served assets with synthetic API responses, including
hierarchy geometry and running-at-100% to succeeded progress/download transition.
Remote runbook and rollback overlay updated; local preview server stopped.


2026-09-20 release preparation: reserve equal status/action height during progress
and download; Chromium verifies unchanged recent-history position. Added the supplied
Markweave logo and a compact vector browser/navigation icon. Version is locally 0.7.0.
Frontend checks (225 tests), production build, Ruff and ty pass. Focused Python checks
pass (56 tests) with the pinned Pandoc on PATH; the first engine run failed because
Pandoc was absent from PATH, then passed after correcting the command environment.
The canonical filtered suite is running again. Final-image harness now includes
presentation and UI acceptance scenarios; these new harness invocations remain pending.
Publication is blocked before any push: OpenAPI comparison correctly classifies new
JobOutput response enum values pptx/pptx-bundle as incompatible. User direction was
requested per yolo's stop-on-failure rule; no compatibility bypass was implemented.
No PR, merge, release, image adoption or branch cleanup has occurred.

2026-09-20: User approved the exact 0.6.4 -> 0.7.0 PPTX response enum
exception. It accepts only the two known new values and exact enum sets, with
regressions rejecting other versions, additional values and unrelated route removals.
OpenAPI comparison now passes; 23 contract tests pass. Remaining slide-oriented
reverse scope is preserved in T83 / G1L-582, not claimed by this forward release.
