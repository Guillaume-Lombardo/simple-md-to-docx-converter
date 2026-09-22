---
ticket: T73
linear_id: G1L-541
linear_url: https://linear.app/g1lom/issue/G1L-541/t73-verify-and-document-reverse-conversion-end-to-end
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T73 - Verify and document reverse conversion end to end

## Objective

Harden, document, and verify the complete reverse-conversion workflow against the exact final
backend, frontend, and reverse-attempt images in both storage profiles.

## Acceptance criteria

* Extend the atomic release set with the third public reverse-attempt image, pinned by immutable
  digest and built from the same reviewed source identity as the matching backend and frontend
  images. Build the exact final three-image set once and run the complete authenticated Revert
  workflow with Playwright against standalone SQLite/filesystem and distributed PostgreSQL/S3
  profiles.
* From the exact same final backend image, run the installed HTTP-only `markweave` reverse-job CLI
  end to end against both storage profiles. Verify human and JSON output, stable exit codes, login
  profile isolation, submission, listing, status/polling, cancellation, and result download with two
  regular users and one administrator. Cover the primary successful lifecycle plus non-enumerating
  cross-owner/admin access, scanner rejection, unsupported or `needs_ocr` input, capacity failure,
  cancellation before publication, expiration, and backend-unavailable behavior; inspect the
  downloaded Markdown/package rather than accepting command success alone.
* Cover at least one representative file from every format family approved by T69, including
  structure and deterministic ZIP/Markdown/assets inspection rather than download success alone.
* Validate the versioned `GET /api/v1/reversions/capabilities` schema, OpenAPI representation,
  authenticated private/no-store contract, deterministic parity across both storage profiles, and
  exact agreement with configured server admission. Prove in browser E2E that the visible hint,
  file chooser, and client preflight derive from the response without a duplicate format matrix,
  update when configured constraints change, and disable submission safely for unavailable or
  unsupported-version capability responses.
* Verify owner isolation, scanner rejection, unsupported/encrypted/malformed input, resource
  limits, capacity responses, idempotency, cancellation, expiration, restart recovery, lease
  recovery, concurrency, absence of double execution, backend/frontend outages, and scanned/image-
  only PDF `needs_ocr` behavior with no network fallback.
* Prove the final backend runs anydoc under arbitrary UID, read-only root, no added capabilities,
  bounded `/work`, and the approved no-document-egress policy; retain bounded failure artifacts only
  on failure.
* Prove the exact final image requests no GPU or accelerator resource, loads no ML runtime, and
  starts no browser, Pandoc, LibreOffice, or other document-engine subprocess during reverse jobs.
  Record CPU time, wall time, peak memory, threads, and concurrency behavior against the approved
  T69 low-compute envelope.
* Exercise result integrity: safe root Markdown filename, safe `assets/` paths, exact relative image
  references, decoded media signatures/extensions, rejection of non-image/mismatched/polyglot and
  animated/multi-frame assets, hostile-SVG sanitization and network isolation, no orphaned assets,
  deterministic ordering/digest, private download headers, and the T70-generated content-free
  traceability manifest.
* Prove with two regular users and one administrator that reverse source, status, cancellation, and
  result routes are owner-only and non-enumerating, including administrator denial on those routes.
  Qualify the existing content-free operational metrics and the existing authorized administrator
  surface exposing immutable, content-free audit records; neither surface may reveal filenames,
  Markdown, assets, content-derived digests, or a download capability. A new reverse-job
  administrator view is not required.
* Extend selective and scheduled CI domains, SBOM/vulnerability/license evidence, mutation scope
  where risk-ranked, and release-install verification for the native anydoc dependency.
* Complete user, API, operations, security, configuration, supported-format, limitation,
  troubleshooting, backup/restore, and upgrade/rollback documentation. State explicitly that OCR
  and hosted Firecrawl fallback are absent.
* Run every applicable canonical Python, frontend, contract, container, integration, E2E, and
  documentation check; no integration or E2E waiver is allowed for this delivered user-visible
  workflow.

## Dependencies

* T21
* T22
* T23
* T46
* T50
* T67
* T70
* T71
* T72

## Implementation boundary

* Own final-image integration, two-profile browser/API/CLI acceptance, selective CI wiring, cross-
  cutting documentation, and release-readiness evidence.
* Begin only after T46, T50, and T67 complete their baseline policy, documentation,
  acceptance, and JavaScript-tooling ownership. Add narrowly scoped reverse-conversion extensions
  to the established surfaces without reopening those tickets' baseline decisions.
* T48 completion and its remaining CI gate integration are not prerequisites, directly or through
  T50. Preserve the delivered mutation baseline and extend relevant reverse-conversion targets
  without requiring contributor-immutable enforcement. T50 remains a blocking qualification dependency.
* Do not edit `SECURITY.md`, `SUPPORT.md`, README, `docs/index.md`, shared cross-guide navigation,
  T67's finalized package-manager/bootstrap/workspace contract, or T48's baseline mutation runner/
  gate unless ownership is explicitly transferred. Put reverse documentation in dedicated guides,
  use the toolchain made normative by T67, and extend only the approved reverse mutation targets
  through T48's established extension mechanism. T73 does not independently select pnpm, Corepack,
  or another package manager.
* Do not expand the approved format matrix or add OCR.
* Do not publish a release or change a public deployment digest without a separately approved
  release ticket.

## Quality requirements

* Preserve the matched backend/frontend/reverse-attempt release identity and existing production
  routing/security contracts.
* Treat regression beyond the approved CPU, memory, thread, or concurrency envelope as blocking.
* Require independent review and exact-head plus exact-main CI evidence before completion.
* Keep repository artifacts and user-facing text in English.

## Progress

* 2026-09-22: The clean canonical run at `27eba5082bb299f343ab3ab49420dbea65e7d9e3` passed 4,525
  tests, with 56 engine-marked tests deselected and 11 warnings in 1,662.28 seconds. Coverage was
  94.97% overall, 91.23% branches (6,408/7,024), and 100% of changed lines (15/15) from base
  `582886e5c87799b6de19a5fc3c0369916055f7a0`; the earlier stale harness-order failure is now closed by the full run. A local normal merge
  of reviewed T48 head `1bb4ca1b55588dc7b254d2a006b3d2df764a1056` was independently approved with
  207 policy tests and global static checks, with no application-source diff against the canonical
  result. The local adoption does not imply T48 main/PR completion. The actual 28-target campaign
  then killed all 28 mutants in 55 seconds, with all six failure statuses at zero and domain counts
  4/5/5/6/5/3; its generated report remains uncommitted. T50 and T85 closure mirrors and the T74
  Backlog mirror are in the tracking scope. Remaining gates are exact final PR/main CI, including
  the 56 engine-marked cases, verified main integration, and separate user clarification on the
  literal public image publication criterion; no release is authorized.
* 2026-09-22: The user selected qualification of the existing administrator surfaces. The acceptance
  criterion now requires owner-only, non-enumerating reverse routes with administrator denial,
  content-free operational metrics, and the existing authorized administrator surface exposing
  immutable, content-free audit records. It does not require a new reverse-job administrator view.
  This resolves the earlier scope ambiguity without waiving the criterion. Remaining current gates are the actual 28-mutant
  campaign, engine-marked CI, exact-head and exact-main checks, and any release/publication decision;
  T73 remains In Progress.
* 2026-09-21: Final-source local qualification completed both full, unmodified profile workflows at
  `c6ff8b1e3913eda1c8103260d37bce00ca106567` against one matched backend/frontend/reverse-attempt
  candidate image set; each profile exited 0. The tracked runner hash, exact local image IDs and
  digests, independent E2E/resource review, 21-case resource measurements, CI-mode image scan
  counts, and complete bundle inventory hashes are recorded in
  [T73 qualification evidence](../docs/evidence/t73-reverse-conversion-qualification.md). These are
  local candidate identities; no public third image, version, or release was selected. At the
  earlier canonical run `5f90757`, 4,520 tests passed but one stale harness-order assertion failed;
  56 engine-marked tests were deselected and 11 warnings were reported. Its corrected 44 focused
  CLI/harness tests plus global Ruff and `ty` checks passed at `c6ff8b1`; this does not convert the
  full canonical invocation into a pass. T73 remains In Progress: the administrator operational-
  metadata criterion still needs the user's decision, the 28-mutant campaign has not run, engine-
  marked tests and exact-head PR/main checks remain outstanding, and public release qualification
  is separate work.
* 2026-09-21: The canonical local suite at `5f90757` completed with 4,520 passed, 56 engine-marked tests deselected, and one stale harness-order assertion: the intentional held-queue CLI phase added a second invocation. The corrected assertion verifies both invocation contexts and flags; all 44 focused CLI/harness tests pass. Global Ruff formatting/lint and ty pass. Coverage is 94.97% overall, 91.23% branches (6,406/7,022), and 100% of changed application lines (15/15). This records the failed complete invocation and successful targeted correction separately; exact-head CI and complete final-image runs remain required.
* 2026-09-21: Independent review approved the scoped worker reconciliation fix. Actual worker-crash and broker-crash recovery diagnostics now pass in both storage profiles, including persisted fencing, frontend outage, and restored browser download matching the retained result digest. Application images are bound to `ed67b098`; the latest reviewed test harness is `5f90757`. These diagnostic phase runs do not replace the complete tracked final-image workflow. The administrator-metadata criterion still awaits the user decision recorded below.

* 2026-09-21: Both-profile diagnostic browser/API/installed-CLI and eight-family corpus runs passed, including hostile extracted assets, capability failures, expiry, unavailable backend, and held-queue quota/cancellation. These use retained diagnostic images, not final qualification. The independently reviewed exact-unit interruption barrier exposed a worker/broker readiness deadlock: reconciliation was gated by the readiness it must restore, with durable ACK ordering also relevant. A scoped production fix and independent review are in progress. Lifecycle recovery, fencing/fairness results, broker restart, and the final reviewed image qualification remain unverified; no acceptance criterion is waived.

* 2026-09-21: Qualification found an acceptance ambiguity: no administrator reverse-job operational view exists. Current reverse routes remain strictly owner-only; aggregate metrics are content-free, and the existing administrator audit reader covers other domains. The specification permits but does not require a new operational view. A user decision is pending between qualifying the existing surfaces with an explicit criterion correction and adding a separately authorized, access-audited view. The acceptance criterion has not been changed or waived.

* 2026-09-21: The diagnostic matched local image set now proves the authenticated CSV API lifecycle in both storage profiles, including scanning, persistent queueing, host-native mTLS broker execution, networkless rootless attempts, Markdown inspection, and administrator result denial. Distributed qualification uses one reverse-enabled worker and one forward-only worker: broker authority is host-wide and reconciliation is principal-exclusive. This does not claim multi-host reverse-worker scaling. The installed CLI positive lifecycle also reached package inspection; negative phases and browser/recovery/concurrency acceptance remain in progress.
* 2026-09-21: Independent review approved three-image release plumbing after preserving historical two-image recovery. New schema-2 publication requires all three identities and preflights every immutable tag; historical schema-1 recovery requires the already-published pair to match and cannot publish a partial historical pair. No public release, version change, or deployment pin was selected.
* 2026-09-21: The diagnostic reverse-attempt image passed the existing containment smoke and the T69 in-image resource probe. CSV stress used 447 ms CPU and 230,916 KiB peak RSS under the existing one-CPU/256-MiB test containment. The receipt distinguishes sampled process observations from stronger containment evidence and does not invent numeric production thresholds. Final qualification must repeat against the stable reviewed image set; current diagnostics do not replace that gate.

* 2026-09-21: Started after T50 was verified on main `6bae1e4d4abc14c44dfca32752bc422d0dfd2502` with successful CI 35629775995; all other recorded dependencies are Done. Extend the existing two-profile E2E harness with the exact reverse-attempt image and a host-native mTLS broker, reuse the T69 corpus and installed CLI/browser workflows, and extend existing atomic release tooling to three images. File ownership is split between E2E/broker orchestration, release tooling, and CLI acceptance; independent reviews remain mandatory. No new framework, release version, public image pin, or T74 implementation is authorized by this start.

* 2026-09-21: The user approved removing the indirect T48 blocker by removing T48 from T50's
  dependencies. T73 still waits for T50 qualification; all final-image, two-profile, security,
  documentation, and release acceptance criteria remain. No organization, Enterprise subscription,
  repository transfer, or contributor-immutable gate is required. T73 remains Backlog.

* 2026-09-03: Created from the approved feasibility decomposition; blocked by T70, T71, and T72.
* 2026-09-03: Final-image acceptance now includes explicit CPU-only proof and measured low-compute
  regression gates.
* 2026-09-05: Product-manager decision deferred the third public reverse-attempt image from T70 to
  T73. T73 now owns its atomic inclusion in the final matched release set; T70 and T71 may use only
  locally built or CI-built exact images before that publication boundary.

* 2026-09-19: User excluded T48 from this qualification follow-up. T48 is no longer a
  blocking dependency and its baseline mutation gate remains out of scope. T71/T72 are
  verified Done on main; all other acceptance criteria and dependencies remain.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
