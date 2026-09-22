---
ticket: T90
linear_id: G1L-589
linear_url: https://linear.app/g1lom/issue/G1L-589/t90-deliver-composer-chat-proposals-and-revision-previews
status: Backlog
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

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-589.
