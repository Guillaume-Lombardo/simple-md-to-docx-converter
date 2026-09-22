---
ticket: T88
linear_id: G1L-587
linear_url: https://linear.app/g1lom/issue/G1L-587/t88-specify-composer-and-qualify-document-editing-and-previews
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T88 - Specify Composer and qualify document editing and previews

## Objective

Formalize the authorized Composer programme in the normative product specification before
production implementation. Qualify targeted editing of real DOCX/PPTX and private DOCX/PPTX/PDF
previews with reproducible, redistributable fixtures.

## Acceptance criteria

- Specify explicit Convert | Composer navigation, retention of `2docx`/`2pptx`/`2md`, file/draft
  handoff, optional permission-gated LLM use, and outage access to drafts and existing exports.
- Define backend-only OpenAI-compatible instance/personal connections, shared/individual identities,
  API keys, mTLS/internal CA, protected secrets, destination policy, and identifiable transmitted data.
- Define reviewable proposals, missing-data and ambiguity questions, private/explicitly shared
  author directory followed by cited library, permission checks before transmission, typed
  repeatable templates, immutable revisions, optimistic concurrency, and copy-forward restore.
- Define structured, validated DOCX/PPTX edits on copies with no arbitrary code or shell;
  unsupported operations fail explicitly, non-target content is preserved, and approved frozen
  data/template can regenerate without an LLM call.
- Evaluate `docx-preview`, `@aiden0z/pptx-renderer`, and PDF.js on real files under the exact CSP:
  fidelity and pagination, complete-file parse cost, long-document performance, visible-page/slide
  virtualization, neighbour preloading, bounded caches, thumbnails, and stale-render fencing.
  Record measured evidence, unsupported cases, package provenance, and required security decisions.
  Require native DOCX and PPTX viewing for the first delivered Composer preview; an explicitly
  labeled PDF rendition may supplement it but cannot substitute for native Office viewing.
- Record feasibility of the user-approved route-scoped native-viewer boundary: Composer parent `frame-src 'self'`
  only, unchanged parent script/style policy, opaque `sandbox="allow-scripts"` child without
  `allow-same-origin`, child-only inline styles, nonce-bearing self-hosted classic scripts, no CORS
  relaxation, no child fetch/workers/objects/forms/navigation, and validated bounded embedded
  images/fonts. Assign hostile-document, message/revision, visual-fidelity, virtualization, and
  media-heavy proof to T90 before production use; assign each Office edit family to T92 before
  enabling it.
- Preserve Next.js/FastAPI authority, both storage profiles, bounded async execution with no worker
  waiting for human input, the existing scan-before-persist upload boundary, and the document
  engines' network isolation. Assign browser connection setup to T89.
- Map all requirements and test cases to T89–T93; keep visual comparison, preview section selection,
  and ONLYOFFICE as future options. Obtain independent review of the specification and evidence.

## Dependencies

T04, T12, T13, T20, T21, T45, T64, T82, T83, T87.

## Implementation boundary

Own the normative contract and reproducible qualification evidence. Do not introduce production
routes, dependencies, storage schemas, browser workflows, or claims of unmeasured fidelity.
Measured real-file feasibility, known unsupported/unmeasured cases, and explicit T90/T92/T93 proof
gates suffice for T88; production viewer/workflow and each Office edit family remain downstream.

## Progress

- 2026-09-22: T88–T93 and their Linear blocking relations were created and verified. Clean baseline
  source is `a879457ce6ab1114b3f3f08c403fb6a7c557813b`, version `0.7.3`; baseline main CI passed.
  Specification and real-file qualification have separate file ownership. Existing docker-box test
  site is `https://markweave.g1lom.xyz`; deployment procedure is being revalidated. The user
  authorized `https://litellm.g1lom.xyz` for later real-provider tests with an economical model
  chosen from its actual catalog. This is an operational preference, not a product restriction.
  Credentials remain unread pending VM 1Password CLI validation. No production implementation,
  pull request, deployment, or release is claimed.
- 2026-09-23: The draft specification and ticket mirrors pass `git diff --check`. Targeted
  `uv run pytest tests/test_documentation.py` executed all nine documentation tests successfully,
  but the command exited 1 because its partial selection triggered repository-wide coverage gates:
  4.08% total against 90% and 0.11% branch coverage against 90%. This is a failed command, not a
  passing suite claim. Full canonical validation and independent review remain pending; no
  qualification, implementation, or deployment acceptance is claimed.
- 2026-09-23: The user decided that first-delivery Composer must include qualified native DOCX and
  PPTX previews. PDF-first substitution is not accepted. This does not approve a CSP change or
  claim renderer fidelity; exact-CSP and real-file qualification remain open.
- 2026-09-23: Independent contract review approved the specification and the scan-before-persist,
  browser connection-setup, and `/composer` routing clarifications. The reproducible
  [qualification report](../spikes/composer/README.md) records bounded real-file probes, not
  production acceptance: high-level saves changed 8/14 DOCX and 21/25 PPTX package entries,
  while one validated text-node edit changed only its target part in each; whole-paragraph DOCX
  replacement lost run styling. Under the exact production CSP, native DOCX/PPTX viewers rendered
  content but their injected styles were blocked. A diagnostic opaque-origin sandboxed frame
  rendered real DOCX/PPTX with styling confined to the child and blocked its tested parent-DOM and
  network access; it needs a narrowly scoped parent framing rule, child styling policy, and
  separately reviewed messaging/asset controls. This prototype does not approve any CSP change or
  establish visual fidelity, hostile-document safety, long-file budgets, or support for the other
  Office edit families. T88 remains In Progress pending the explicit security decision and further
  qualification; T89 production work has not begun.
- 2026-09-23: Independent review reproduced the final [bounded native-preview spike](../spikes/composer/README.md):
  a classic bundle rendered real DOCX and PPTX inside an opaque sandbox without relaxing CORS.
  The child could not access the parent DOM or fetch its own/external documents; zero external
  requests were observed. The proposed Composer-only parent `frame-src 'self'` allowance and
  child-only inline styling await the user's explicit security decision. Parent script/style
  restrictions stay unchanged; the child omits `allow-same-origin` and blocks fetching, workers,
  objects, and forms. This is feasibility evidence, not production safety, visual fidelity, or T88
  completion. Remaining gates include policy containment, hostile-document/network tests,
  message/revision concurrency, representative Office visual and preservation evidence, and
  media-heavy long-document memory, virtualization, and cancellation. Text-node preservation does
  not qualify the other edit families. Native DOCX/PPTX previews remain mandatory in first delivery;
  no push, pull request, deployment, version bump, or release has occurred.
- 2026-09-23: After independent feasibility review, the user explicitly approved the narrow native
  preview boundary: `frame-src 'self'` on the Composer parent route only, with its existing script
  and style restrictions unchanged; an opaque `sandbox="allow-scripts"` child without
  `allow-same-origin`; child-only inline styling and nonce-bearing self-hosted classic scripts;
  no CORS relaxation; no child fetches, workers, objects, forms, or navigation; and only validated,
  bounded embedded images/fonts. This supersedes the earlier pending-decision note but does not
  qualify production security, fidelity, or T88 completion. Hostile-document containment,
  message/revision races, representative visual comparisons, and media-heavy long-document
  memory/virtualization/cancellation still require evidence. T88 remains In Progress.
- 2026-09-23: The expanded [T88 qualification report](../spikes/composer/README.md) covers one
  narrow text replacement in each Office format plus eight additional representative family
  operations: DOCX table, section, image and style; PPTX table, slide order, shape/object and note.
  Each surgical fixture edit
  changed only its requested uncompressed OOXML member and reopened or inspected the target;
  high-level library saves still changed unrelated parts. These examples do not qualify a general
  untrusted-document executor or unchanged visual behavior. In the approved isolated preview
  candidate, tested DOCX links, injected same-origin/external image and CSS URLs, and an inline
  handler were blocked without an external request. The message probe accepted one intended
  revision and rejected sibling-frame, wrong-token and stale-revision responses; overlapping
  renderer completions remain untested. A 2,000-paragraph DOCX built the full DOM in one section
  without Word-like natural pagination; a 120-slide PPTX initially materialized two slides in
  about 1.1 seconds. The one-run heap sample and small repetitive media do not establish a peak or
  production budget. T90 still owns hostile-document/navigation and message concurrency tests,
  exact visual comparisons, cache/virtualization and media-heavy limits; T92 owns safe execution
  and preservation proof for each edit family. T88 remains In Progress until reviewed on `main`.
- 2026-09-23: The qualification agent reported exit 0 for the exact `uv run --with
  python-docx==1.2.0 --with python-pptx==1.0.2 --with pypdf==6.1.1 python
  spikes/composer/office_family_probe.py` command (eight successful family cases), the isolated
  Chromium preview probe, the strict-CSP baseline probe, scoped Ruff lint/format checks, Prettier
  check, and frozen pnpm installation using the repository's reviewed bootstrap. An initial bare
  `pnpm install` invocation failed because the ambient mise shim lacked the expected version; the
  reviewed bootstrap path resolved that tool invocation. Independent final evidence review and
  repository canonical checks are still pending.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-587.
