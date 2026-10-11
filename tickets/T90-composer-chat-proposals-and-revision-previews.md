---
ticket: T90
linear_id: G1L-589
linear_url: https://linear.app/g1lom/issue/G1L-589/t90-deliver-composer-chat-proposals-and-revision-previews
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T90 - Deliver Composer chat, proposals and revision previews

## Objective

Deliver the usable Next.js Composer workspace using the T88-reviewed native-preview candidate and
the T89 backend, then qualify the viewer and workflow for production.

## Acceptance criteria

- Put explicit English Convert | Composer selection near the logo; retain `2docx`, `2pptx`, and
  `2md` and preserve selected files, assets, and drafts when opening Composer.
- Place chat left and qualified DOCX/PDF/PPTX preview right; support Markdown drop, authorized model
  selection, message/form questions, and expandable cards with results, sources, changes, and errors.
  First delivery includes qualified native DOCX and PPTX preview; a labeled PDF rendition is only
  an additional view and cannot satisfy native Office-preview acceptance.
- Let users accept, edit, or reject proposals; surface missing information and ambiguity, preserve
  human corrections, and distinguish facts and citations from unsupported assertions.
- Bind preview and download to one exact revision; show history, highlighted semantic diffs, and
  copy-forward restore. Preserve zoom, position, and prior preview during updates, fence obsolete
  renders, virtualize visible pages/slides, preload neighbours, bound caches, and load thumbnails
  progressively.
- Document measured fidelity, DOCX pagination limits, and complete-file download/parse behavior.
  Use the approved Composer-only `frame-src 'self'` parent allowance with otherwise unchanged
  parent script/style restrictions. Render native Office files in an opaque
  `sandbox="allow-scripts"` child without `allow-same-origin`, with child-only inline styling,
  nonce-bearing self-hosted classic scripts, no CORS relaxation, no child fetch/workers/objects/
  forms/navigation, and validated bounded embedded images/fonts. Prove message source, token and
  exact-revision binding, hostile-document containment, no egress, and visual fidelity.
- Verify authorization, disabled/outage/reconnect states, uploads, concurrency, revisions, and long
  documents with browser tests and final-rootless-image E2E in both profiles. Obtain independent
  review, required checks, and qualified docker-box candidate deployment.

## Dependencies

T89.

## Progress

- 2026-09-22: Created in Linear and mirrored before implementation. No delivery is claimed.
- 2026-09-23: Started the bounded Composer workspace and native-preview implementation from
  the merged T89 foundation. Linear synchronization is pending because the current OAuth session
  is expired; the project owner explicitly authorized continuing with this local mirror. T89's
  docker-box deployment remains an acceptance gate, not a claim that T90 is accepted.
- 2026-09-23: Implementing durable model questions, human-reviewed Markdown revisions, one-action
  asynchronous DOCX/PDF/PPTX generation from approved content, owner-scoped Convert/2md handoff,
  and native private previews. Independent reviews identified and drove fixes for local persistence
  races, outbound authorization, exact preview/download binding, and the child script policy.
  Native Office fidelity, bounded media-heavy performance, final canonical coverage, both final
  image profiles, and matched docker-box deployment remain open acceptance gates. Earlier visual
  comparisons used a stale generated child bundle and are being repeated with served-byte
  traceability; no viewer qualification is claimed from them.
- 2026-09-23: The T89 Admin correction was merged as PR 268 at `9b1828d`; main CI run
  `35871945081` attempt 2 passed with both final rootless image profiles, and the reviewed
  consolidated deployment script was delivered. T89 remains **In Progress** until the matched
  docker-box deployment can be verified. Linear synchronization for
  T89 and T90 remains deferred under the owner's explicit temporary authorization.
- 2026-09-24: Diagnostic checks and browser runs exposed stale fixtures, a broker shutdown bug,
  a browser-runner memory collision, a soft-navigation CSP transition, and captured Markdown
  submitted before approval. Their original failures remain in the delivery logs; focused fixes
  were independently reviewed and superseded by the final candidate checks below.
- 2026-09-23: The owner clarified that the existing docker-box endpoint is the test instance and
  authorized replacing it with a working, matched T90 candidate while preserving its current
  password, accounts, data, scanner, broker mTLS, and rollback set. This is a scoped test-instance
  deployment decision, not a general change to the public release contract. Candidate publication,
  independent deployment-plan review, and live verification remain pending; no remote deployment
  has been performed. The development VM was enlarged online to 150 GiB after a verified backup.
- 2026-09-24: Candidate source `2b88914f8831222302cab3893073a1ce54a44fc7` has independently
  reviewed backend, workspace, native-preview, broker-signal, CSP-navigation, generation-source,
  and E2E harness/fixture changes. The final web check passed 626 tests, formatting, lint, types,
  bindings, and structure, with 3,186/3,535 branches covered (90.12%). The default canonical
  Python suite passed 5,178 tests with 59 engine-marked cases deselected on the same Python and
  configuration bytes later committed in `2b88914`; branch coverage is
  7,702/8,550 (90.08%) and the official changed-line check is 974/1,076 (90.52%). Ruff format,
  Ruff lint, and `ty` passed. The host engine-inclusive `uv run pytest` completed with 5,188
  passes and 49 failures, all in `tests/integration/document_engines/`: this development host
  lacks the pinned Pandoc, LibreOffice, Mermaid/Chromium, font, and `/opt/toolchain` evidence
  environment required by those tests. This is a failed local full-suite gate, not a pass or
  an approved exception.
- 2026-09-24: The matched final rootless images passed the complete unattended standalone and
  distributed E2E profiles, including real Composer questions/proposals, human-approved
  DOCX/PDF/PPTX generation, native previews, exact revision downloads, held-job cancellation,
  authorization, scanner failure/recovery, migrations, and one-signal broker restart. The browser
  runner has its own 2 GiB limit while the application retains 768 MiB. Reviewed native-preview
  probes passed real Office content, hostile-input containment/no egress, bounded visible-unit
  rendering, and in-bounds grouped PPTX content; DOCX pagination and floating geometry remain
  approximate. A 5,958,454-byte media-heavy DOCX reached 195,858,421 bytes of sampled transient
  child heap, so broader device capacity remains unqualified and is not advertised.
- 2026-09-24: These results qualify the local test candidate, not a live deployment or completed
  T90 acceptance. The owner-authorized docker-box test-instance cutover still requires matched
  image transport, password/data-preserving operational preflight, live smoke, and rollback
  verification. T89 and T90 remain **In Progress**. Linear synchronization remains deferred under
  the owner's temporary authorization while OAuth is expired.
- 2026-10-11: Completed and verified on `main`. Every acceptance criterion is evidenced:
  - The Convert | Composer selection, chat and native DOCX/PPTX preview with a labeled PDF view,
    durable questions, proposal accept/edit/reject, exact revision-bound preview and download,
    history and copy-forward restore, bounded virtualized rendering, the Composer-only
    `frame-src 'self'` allowance, and the opaque `sandbox="allow-scripts"` child were delivered
    by PR #269 (`57521ed62eb19558b945e1109934bcc6bb967ac8`) after independent focused reviews.
    PR CI run 35961177550 and main CI run 35964148193 passed, including both final
    rootless-image E2E profiles and `container`.
  - The Composer decision flows were requalified on `main` after the T104 merge. Main CI run
    38142225187 on `d1049e8` had failed `e2e-standalone` twice in the Composer accept/reject
    steps. The cause was a Composer workspace race that predates T104: the workspace read the
    draft, proposals, and questions in parallel, so it could show a new proposal with an older
    draft ETag, and the decision request returned HTTP 412. PR #292 fixed the race, so the
    workspace reads one consistent draft snapshot before any decision; it was squash-merged as
    `6ee903b6df9b7db2968ad9d24a53ef3346bcf5ac` after independent review approved it with no
    findings. Full-suite main CI run 38150642257 at `6ee903b` passed, including both final
    rootless-image E2E profiles, `container`, `compose`, `document-engines`, and both storage
    profiles.
  - Measured fidelity, DOCX pagination limits, complete-file download/parse behavior, hostile-
    document containment, no egress, and same-source visual comparisons are documented in
    `docs/composer-preview.md`. That document keeps varied document fidelity, true process peak
    memory, PDF performance, and safe device capacity explicitly unqualified; the visual
    evidence covers the recorded fixtures only.
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
    uses the cold copy, as the docker-box deployment notes document. The owner set the rollback
    window to 2026-10-18. The broker mTLS certificates were renewed on 2026-10-11 and now expire
    on 2027-10-11 at 14:02 UTC. The owner then checked the browser UI. One Composer generation
    was refused at validation with "Markdown input contains raw HTML." because the draft
    contained `<stack>` outside a code span; the refusal is correct, but the UI does not show
    the failure reason, which follow-up T131 / G1L-684 covers. Distributed restore also refuses
    recovery sets that contain jobs on superseded template versions; this defect predates the
    deployment and is tracked by T132 / G1L-685. Intermittent final-image E2E scanner-outage
    timeouts are tracked by T130 / G1L-683.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-589.

- 2026-09-26: Linear access returned. G1L-589 now records In Progress, the protected PR #269
  merge to `57521ed62eb19558b945e1109934bcc6bb967ac8`, passing hosted CI run 35961177550,
  and the remaining matched docker-box deployment, live smoke, and rollback gate. Its original
  native-preview decision, acceptance criteria, High priority, and T89 dependency were preserved.
  No completion is claimed.
