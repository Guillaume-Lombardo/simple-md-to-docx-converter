---
ticket: T92
linear_id: G1L-591
linear_url: https://linear.app/g1lom/issue/G1L-591/t92-deliver-bounded-office-editing-tools-and-cited-document-library
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T92 - Deliver bounded Office editing tools and cited document library

## Objective

Deliver the T88-qualified structured Office operations and a permission-aware cited reference
library after the author directory is available.

## Acceptance criteria

- Read and modify real DOCX text, tables, sections, images, and styles and real PPTX slides, text,
  tables, images, styles, objects, and notes through bounded schemas and stable targets. FastAPI
  validates and executes on copies without arbitrary shell/code; only qualified operations are
  exposed and unsupported operations fail explicitly.
- Preserve non-target OOXML, formatting, relationships, and media, and verify real-file results,
  controlled revisions, exact downloads, diffs, and restore. Reject unsafe archives/XML, embedded
  active content, external loading, and invalid targets without publication.
- Add permissioned library search and source-version citations. Filter access before retrieval or
  transmission, revalidate after revocation and on publication, and leave user review explicit.
  Scan every new library upload before indexing or persistence; an authorized immutable conversion
  handoff may reuse verified scan provenance, while changed or unattested bytes require a new scan.
  Test infected inputs and unavailable scanners fail closed.
- Test prompt injection/tool misuse, invalid responses, preservation, concurrency, recovery,
  permissions, and long files with unit, real-boundary integration, and final-rootless-image E2E
  proof in both profiles.
- Complete independent review, required checks, and qualified docker-box candidate deployment.

## Dependencies

T91.

## Progress

- 2026-09-22: Created in Linear and mirrored before implementation. No delivery is claimed.
- 2026-10-21: Started from `main` `95bc188` after T89–T91 reached Done; G1L-591 moved to In
  Progress. First increment: the DOCX body-text edit family only.
  `markweave.composer.docx_edits` admits a private copy under the existing bounded Word
  archive/OPC checks (active content, signatures, and every external relationship except
  never-fetched hyperlinks are rejected), lists stable `word/document.xml#t<n>` text targets with
  paragraph and table context, and replaces whole `w:t` nodes under an expected-text
  precondition by splicing exact bytes. Every other package member and byte is preserved and
  re-verified after writing; tracked changes, content controls, field results, alternate
  content, self-closing, attributed, and control-character nodes are never targets.
  `GET .../revisions/{revision_id}/docx-text-targets` and
  `POST .../revisions/{revision_id}/docx-edits` (current revision only, `If-Match`,
  `Idempotency-Key`) publish an immutable revision with matching DOCX download/preview, frozen
  operation record, `human:docx_edit` provenance, and lineage; failures return
  `COMPOSER_DOCX_EDIT_*` with target identities only. OpenAPI, the T41 fixtures, and the web
  bindings are regenerated; the API and user guides document the operation and its limits.
  Unit (100% branch coverage of the executor), SQLite HTTP integration, real-corpus
  preservation, and Pandoc-generated DOCX tests pass locally. The LibreOffice rendering test
  could not pass on this host: its LibreOffice 24.2.7.2 differs from the pinned 26.2.5.2
  toolchain and the existing T91 rendering test fails identically on `main`. Open in later
  increments: table, section, image, and style structure edits; every PPTX family; model
  tool-call proposals using the same executor; semantic DOCX diff; CLI and browser controls;
  the permissioned cited library with scanned uploads; PostgreSQL/S3 and final-rootless-image
  E2E proof in both profiles; independent review and docker-box deployment. No acceptance
  criterion is claimed complete.
- 2026-10-21: Implementation-host verification of the first DOCX text-edit increment: `uv sync
  --all-groups`, Ruff format/lint, and `ty` pass. The canonical engine-excluded suite ran 6,294
  passed, 8 failed, and 78 errors in 10m40s. All 78 errors are PostgreSQL, S3, and local-service
  tests that could not start because Docker is unavailable on this host; the 8 failures (engine
  `/proc` isolation, read-only doctor, subreaper harness, and four broker factory tests) fail
  identically on `main` `95bc188` here. Because the Docker-backed suites did not run, the
  aggregate 89.79% branch coverage is not representative; the new executor, schemas, and router
  reach 96–100% in that run, and the executor reaches 100% branch coverage in its focused run.
  The web format, lint, typegen, typecheck, binding, and structure checks pass; the coverage run
  passed 843 of 844 tests, and the remaining existing author-pagination test timed out at 5 s
  under CPU contention and passed 47/47 when its file was rerun alone. Hosted CI remains the
  required proof for the Docker-backed and engine-marked suites.
- 2026-10-21: Review corrections for PR #314. A text node that contains any child element,
  nested text node, comment, or processing instruction is now ineligible, so its markup can no
  longer be silently dropped; later targets keep their ordinals. Executor regressions and an
  HTTP no-publication test cover the reviewed `<w:t>before<w:b/>after</w:t>` case. The SQLite HTTP
  scenarios are now shared with a new PostgreSQL/S3 module covering a successful edit, download,
  and restore, the four rejection paths, request validation, Markdown refusal, ownership,
  four-way concurrent publication, and nested markup. The final-rootless-image Composer browser
  phase, which runs in both standalone and distributed profiles, now uploads the real DOCX
  fixture, checks target listing, foreign-user 404s, rejected edits without publication, a
  three-way concurrent edit with one winner, idempotent replay, exact preview/download bytes,
  the edited text inside the private DOCX preview iframe, a stale-base 412, and an exact restore.
  Locally, 165 focused tests pass, including the 10 new PostgreSQL/S3 tests. They ran against
  native PostgreSQL 16 and a moto S3 endpoint because the pinned PostgreSQL 18 and RustFS images
  could not be pulled (Docker Hub 429; GHCR blob download refused by the proxy). Podman is
  unavailable, so the final-image phase is verified only by hosted CI.
- 2026-10-21: Second increment on `feat/T92-pptx-text-edits`, stacked on PR #314. The executor
  is now a shared `office_edits` core (admission, text-node grammar, byte splicing, verification)
  with two package kinds; DOCX behavior and its public API are unchanged. The PPTX kind lists
  slides in `p:sldIdLst` order, each followed by its notes part, and fails closed on unknown,
  duplicate, mistyped, or shared slide and notes relationships. Whole `a:t` runs are replaced
  without adding attributes; `a:fld` text, alternate content, attributed runs, and runs
  containing markup are excluded. `GET .../pptx-text-targets` and `POST .../pptx-edits` mirror the
  DOCX contract with `COMPOSER_PPTX_EDIT_*` errors and `human:pptx_edit` provenance. Evidence:
  100% branch coverage of `pptx_edits`; real `pres.pptx` edits of all 13 slide, table, group,
  and notes targets change only the four edited parts; a host LibreOffice conversion renders the
  edited deck; a Pandoc 3.1.3 deck edits cleanly; and SQLite and PostgreSQL/S3 HTTP workflows
  cover listing, format mismatch, stale target, publication, replay, restore, and ownership. The
  final-image Composer browser phase now stages `pptx/pres.pptx` and checks the PPTX workflow,
  including the edited title in the private PPTX preview, in both profiles; that phase is
  verified only by hosted CI.
- 2026-10-21: Third increment on `feat/T92-model-office-edits`, stacked on the PPTX branch:
  model-proposed Office text edits. `office-edit-steps/preview` discloses only user-selected,
  currently eligible targets of the current DOCX or PPTX revision with an exact transmitted text
  and digest; `office-edit-steps` starts an ordinary bounded model step (intent `office_edit`, no
  migration) whose disclosure context stays in memory with the running step. The reply is
  accepted only as allowlisted `replace_text` operations on disclosed targets; any other
  operation, target, key, shape, prose, or invalid text fails with `provider_invalid` and no
  proposal. Proposals are canonical JSON with transmitted expected texts; edited decisions may
  only change or drop replacement texts. `proposals/{id}/office-edits` publishes accepted or
  edited proposals through the executor only while the reviewed base revision is current, with
  a dedicated persistence fence that also prevents Markdown/Office provenance confusion in both
  directions. Evidence: 100% branch coverage of `office_edit_proposals`; SQLite and
  PostgreSQL/S3 HTTP workflows cover review, edited decisions, publication and replay,
  eight tool-misuse and prompt-injection replies, stale/foreign/ineligible requests, superseded
  proposals, PPTX notes proposals, Markdown masquerade refusal, and request edges. The
  final-image Composer browser phase now runs a real provider round trip that fails closed on an
  injected undisclosed target and publishes an accepted model proposal; the provider's expected
  successful chat counts rise from 12 to 14 and from 17 to 19. That phase is verified only by
  hosted CI.
- 2026-10-21: Fourth increment on `feat/T92-office-ui-cli`, stacked on the model-edit branch:
  browser, CLI, and diff support for Office text edits. The Composer workspace shows an **Edit
  document text** panel for a current DOCX or PPTX revision with labelled targets (paragraph,
  table, slide, notes), publication of changed runs with their loaded text as precondition, and
  a model section that shares only ticked targets, shows the exact preview, sends it with the
  reviewed digest, polls the step, and hands the proposal to the existing review list. Office
  proposals there show each `expected → new` change and publish through the Office route; the
  Markdown publish button is withheld for them. `markweave composer office targets|edit|propose|
  publish` mirror the API, read operations or instructions only from private files or stdin,
  and confirm the exact transmitted text before a model call. Revision diff now compares two
  DOCX or two PPTX downloads as per-paragraph text (`scope` `artifact`, no contract enum change).
  Evidence: 19 new vitest cases for the API client and panel plus two workspace cases; the full
  web check passes with 90.04% branch coverage; 16 CLI unit tests; HTTP diff assertions for DOCX
  and PPTX in SQLite and PostgreSQL/S3.
- 2026-10-21: Fifth increment on `feat/T92-composer-library`, stacked on the UI/CLI branch: the
  permissioned cited library backend. Migration `20261021_27` adds library documents, grants,
  immutable versions, and numbered chunks plus a nullable `composer_model_steps.library_refs`
  column reserved for citations. Uploads (`.md`, `.txt`, `.docx`, `.pptx`) are bounded, scanned
  before parsing, split into whole-paragraph chunks, and published in two phases (pending row,
  verified object write, finalize) with immediate cleanup of a failed or losing publication and
  startup recovery of interrupted ones; one live publication per document keeps version numbers
  contiguous. Owner-only replace, grant, revoke, and delete use `If-Match` and content-free
  audit events. Listing, search, download, and excerpts filter owner or active grantee in SQL
  before any text is loaded, so hits and citations never grant access. Evidence: 100% coverage of
  `composer/library.py`, 95% branch coverage of the repository, 97% of the router; SQLite and
  PostgreSQL/S3 HTTP workflows for lifecycle and revocation, Office sources, rejected sources,
  concurrent replacements, failed writes and recovery, and request validation. Citations in model
  steps, the library UI and CLI, and their final-image E2E phase remain open.
- 2026-10-21: Sixth increment on `feat/T92-composer-library`: citations, CLI, and browser support.
  Model-step previews accept up to 20 `library_refs` and append the exact authorized excerpts,
  after any author records, as JSON reference data with a `library:{version_id}#{chunk}`
  identity; the start request repeats the references with the preview digest, and the step
  records them (`library_refs` in the step response). Access to every cited chunk is rechecked,
  with the cited documents locked, at admission, before egress, before the result is recorded, and
  at proposal publication, so a revocation or deletion fails closed without transmission or
  publication. `markweave composer library ...` manages and searches the library and
  `model-steps start --cite VERSION_ID:CHUNK` cites excerpts after verifying the preview. The
  browser gains a **Reference library** page (upload, search, excerpt, versions, download,
  share, revoke, replace, delete) and excerpt selection in the model request. Evidence: SQLite
  and PostgreSQL/S3 HTTP workflows for a cited step through publication with revocation before
  publication, plus foreign, missing, duplicated, excessive, stale, unreviewed, revoked-before-start,
  and revoked-during-call citations; combined author-and-citation prompt order; admission
  validation; 23 CLI unit tests and an installed-CLI test against the real application over
  loopback HTTP; 8 new vitest cases (full web check passes at 90.01% branches). The final-image
  Composer browser phase now uploads and searches through the library page, proves sharing and
  revocation, and runs one cited model step through the provider, whose expected successful chat
  counts rise from 14 to 15 and from 19 to 20; that phase is verified only by hosted CI.
- 2026-10-21: Review corrections on the library branch.
  - **Late writes.** An upload paused past its lease can write its object after the deletion's
    sweep has removed every cleanup record, then crash before discarding it. Maintenance now also
    reconciles the `composer-library` object scope through a new optional `ListableObjectStore`
    port (filesystem directory walk, S3 `ListObjectsV2` with a prefix) and removes any object that
    no version row references; a version row always precedes its object and is never reused. The
    shared SQLite and PostgreSQL/S3 scenario reproduces the pause, sweep, write and crash.
    Operational limitation: every maintenance pass (startup and each retention cycle) lists the
    whole `composer-library` scope and checks it against `composer_library_versions` in batches
    of 500, so its cost grows with the number of stored library versions; deletions per pass are
    bounded by the cleanup limit, but the listing is not. A persisted cursor or a sweep journal
    keyed by in-flight uploads would bound it if the library grows large.
  - **Cited admission race.** A cited retry that loses the race to an identical request now
    replays the durable step after the precondition failure instead of returning 412.
- 2026-10-21: Seventh increment on `feat/T92-office-structure-edits`, stacked on the library
  branch: the first structural edit families, qualified on the real corpus files.
  `markweave.composer.office_structure` admits the same private copy and splices exact bytes for
  `set_paragraph_style` (DOCX body paragraphs outside tracked changes, content controls,
  alternate content, and text boxes; existing paragraph styles only), `move_slide` (PPTX
  `p:sldIdLst` order), and `replace_image` (PNG/JPEG parts referenced by the document or slides,
  replaced with a scanned upload re-encoded without metadata into the target's format). Each
  result is re-admitted and every other part, text node, style, and slide entry is verified.
  `GET/POST .../revisions/{id}/{docx|pptx}-structure[-edits]` reuse the text-edit publication
  contract. Evidence: 32 unit tests (95% branch coverage of the engine), SQLite and
  PostgreSQL/S3 HTTP workflows for styles, images, slide order, replay, stale ETag, changed
  targets, infected/malformed/oversized/foreign images, unsupported operations, and the owner
  fence, and a final-image API journey in both profiles, verified only by hosted CI. Tables and
  sections (DOCX), tables, styles, and objects (PPTX), the browser/CLI surface for structural
  edits, and model tools for them remain open.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-591.
