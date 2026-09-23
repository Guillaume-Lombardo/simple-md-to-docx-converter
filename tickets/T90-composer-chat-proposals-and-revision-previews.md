---
ticket: T90
linear_id: G1L-589
linear_url: https://linear.app/g1lom/issue/G1L-589/t90-deliver-composer-chat-proposals-and-revision-previews
status: In Progress
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
- 2026-09-23: The complete default Python suite recorded 5,160 passes and three failures. Two
  stale contract fixtures have independently reviewed, targeted passing corrections; a Unix
  broker BrokenPipe has not recurred in 30 isolated runs or the complete 20-test transport module,
  but its original cause is unproven. Combined coverage from unchanged Python production sources
  reaches 90.09% branches and 90.60% provisional changed executable lines; the official
  commit-based changed-line check remains pending. The final web check passes formatting, lint,
  types, generated bindings, structure, and behavior at 90.08% branch coverage (3,162/3,510).
  Independent reviews cleared the backend, workspace, Admin, handoff, native preview, and narrow
  E2E fixture corrections. The fresh generated and served native-preview child bytes match the
  reviewed source build; real Office files, hostile inputs, long-document windowing, and in-bounds
  grouped PPTX content passed the documented local browser probes. The 5,958,454-byte image-heavy
  DOCX reached 195,858,421 bytes of sampled transient child heap, so broader device capacity
  remains unqualified and is not advertised. The three exact candidate images build; the backend
  image runs SQLite 3.34.1 and passed T90 upgrade, T89 downgrade, and re-upgrade. Final-image E2E
  is still open:
  three stale browser fixture expectations were corrected, then the real Composer test hit a
  confirmed Chrome OOM because the E2E harness ran its browser inside the application's 768 MiB
  cgroup. A separate bounded browser runner is being corrected without changing the application
  image or limit; both standalone and distributed profile terminal results remain required.
- 2026-09-23: The owner clarified that the existing docker-box endpoint is the test instance and
  authorized replacing it with a working, matched T90 candidate while preserving its current
  password, accounts, data, scanner, broker mTLS, and rollback set. This is a scoped test-instance
  deployment decision, not a general change to the public release contract. Candidate publication,
  independent deployment-plan review, and live verification remain pending; no remote deployment
  has been performed. The development VM was enlarged online to 150 GiB after a verified backup.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-589.
