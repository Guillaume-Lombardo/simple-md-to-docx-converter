---
ticket: T89
linear_id: G1L-588
linear_url: https://linear.app/g1lom/issue/G1L-588/t89-implement-secure-composer-connections-and-durable-revision
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T89 - Implement secure Composer connections and durable revision foundations

## Objective

Deliver authorized backend-only LLM connections and durable Composer foundations in both existing
storage profiles.

## Acceptance criteria

- Implement instance/personal connection permissions, shared/individual identity modes, permitted
  model discovery/selection/test, write-only API credentials, mTLS key/certificate/internal CA,
  destination and TLS policy, secret redaction, bounded calls, and safe errors.
- Provide a simple authorized browser configuration flow for instance or personal connections:
  test, model selection, write-only credential and client-certificate entry, internal CA setup,
  rotation, and revocation. Test the complete flow through final-image E2E without exposing secrets.
- Make initial setup discoverable from an Administration hub with LLM settings and existing Templates.
  In the standalone evaluation quickstart, persist a private external envelope key, start with
  model egress disabled, and let the administrator explicitly preview and approve exact destinations
  and resolved addresses before the existing credential, model, test, enable and grant flow. Keep
  production operator ceilings authoritative, fence concurrent policy edits with ETags, and verify
  key identity on restart/restore before any new credential write. A missing or mismatched key must
  leave conversion and authorized retained-draft reads usable while blocking model and credential
  operations. Administrator policy audit follows guarded, bounded retention. On the qualified
  Linux/AMD64 runtime, document engines retain a private network namespace even when backend model
  egress is enabled; no engine-accessible Unix descriptor broker is mounted.
- Advertise distinct configured, enabled, authorized, and outage states; ordinary conversion
  readiness is independent of LLM health, and retained owner drafts/exports remain accessible.
- Persist owner-scoped drafts, messages, proposal states, frozen approved inputs/template/model
  identity, immutable artifact-linked revisions, history, copy-forward restore, ETag/If-Match,
  idempotency, atomic publication, backup/restore, and restart recovery in both profiles.
- Keep model egress separate from network-isolated document engines, release execution resources
  while awaiting human answers, and fence revocation and stale in-flight results.
- Expose documented API and installed CLI parity, migrations, configuration, and English guides.
  Test scan-before-validation/persistence for new uploads, infected inputs, unavailable scanners,
  exact immutable handoff provenance, permissions, secrets, TLS/mTLS, invalid responses, outages,
  cancellation, and concurrency
  through unit, real-boundary integration, and final-rootless-image E2E in both profiles.
- Complete independent review and required checks, then deploy the qualified matched candidate to
  docker-box with rollback evidence.

## Dependencies

T88.

## Progress

- 2026-09-22: Created in Linear and mirrored before implementation. No delivery is claimed.
- 2026-09-23: Started on `feat/T89-composer-foundations` after verified T88 merge.
- 2026-09-23: Independently reviewed candidate includes connection security, browser setup, CLI
  parity, durable drafts/revisions in both profiles, bounded shared-database model-step cancellation
  and periodic restart recovery, transactional content-free audit, and exact artifact/restore
  handling. Real HTTP-to-SQLite-to-private-HTTPS tests cover cancellation, no late proposal, and sole
  execution-slot reuse; PostgreSQL/S3 integration tests also passed locally. The immutable-source
  canonical Python run passed 5,006 tests, with 56 external-engine-marked deselected and 17 warnings
  in 41m04s; total coverage is 94.24% and the repository branch-only gate passes at 90.35%
  (7,374/8,162). Earlier moving-source baselines failed with seven, then two, test failures; the
  related regressions are fixed and covered by the final passing run. Ruff, ty, OpenAPI, and web
  checks pass; the official changed-line gate awaits the candidate commit. The unmocked final-image
  harness covers private HTTPS, mTLS, permissions, outage, scanner failures, cancellation, exact
  revisions, and post-Composer isolated backup/restore, but standalone and distributed images have
  not run. Hosted PR CI will run both profiles after publication. Docker-box SSH agent signing still
  times out, so remote capacity/rollback checks and matched deployment remain pending. Real LiteLLM
  qualification belongs to T93. T89 remains In Progress; no image, deployment, or release acceptance
  is claimed.
- 2026-09-23: Ready PR [#267](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/267)
  published reviewed source `a5988e223a5d6abcdbd5d1faf8d0eb15e510b3f8`; its first hosted
  [CI run 35813722349](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35813722349)
  failed Python coverage and both final-image E2E profiles. The two light artifacts covered only
  86.78% of application branches; the local canonical and changed-line gates had passed at 90.35%
  and 90.64%, respectively. Both E2E profiles stopped at a test expectation of `unauthorized`
  before any connection existed, when the defined initial state is `unconfigured`. All other
  substantive CI jobs passed; the aggregate gate failed as expected. Independently reviewed
  corrective source now checks `unconfigured` initially and
  `unauthorized` after an ungranted instance connection exists. It retains the two disjoint light
  shards and adds an unconditional PostgreSQL/S3 coverage producer with a third same-attempt raw
  artifact; the 90% total, branch, and changed-line thresholds are unchanged. The corrected
  no-Podman cohort passes 178 selected local-boundary tests and 315 CI validation/selection tests;
  a simulation combining authentic CI shard artifacts with the additional real-boundary tests
  passes the official branch and changed-line checkers at 90.33% and 90.64%. The corrective commit
  and hosted rerun are pending; neither final-image profile nor docker-box deployment is accepted.
- 2026-09-23: The second hosted
  [CI run 35816849061](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35816849061)
  at exact PR head `e89bf4c894730d062883edbd24011885ca43a1e1` ended `cancelled`.
  The new unconditional PostgreSQL/S3 coverage job and Python shard 1 passed; shard 0 reached
  99% before its 20-minute job limit cancelled it, so the aggregate coverage job was skipped.
  Both final-image E2E profiles stopped after successful connection metadata creation because
  a page-global secret-field locator matched both the Add form and the new card's rotation form.
  Every other substantive job passed, while the final gate failed. The next corrective candidate
  bounds light shards to 25 minutes without changing their selection or coverage
  contract, and scopes all four secret-field clearing assertions to the Add form in both real and
  mocked browser tests. The mocked browser test and 13 Composer component tests pass locally;
  the real final-image journeys remain unverified after that assertion. The next corrective commit,
  hosted rerun, and docker-box deployment/rollback are pending; T89 remains In Progress.
- 2026-09-23: The third hosted
  [CI run 35819110981](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35819110981)
  at exact PR head `0b5ea7de80479fda2e0abf806356ab4fdccc4630` completed with all
  substantive jobs passing except the two final-image E2E profiles. Both Python shards passed
  within the 25-minute bound (22m01s and 18m20s), and the three-artifact hosted coverage gate
  passed at 90.31% application branches (7,371/8,162) and 90.64% changed lines (3,670/4,049).
  The aggregate gate failed because both E2E profiles stopped at the same test fixture assumption:
  an administrator could manage and test a shared instance connection but had not been granted
  permission to use it, so `unauthorized` correctly took precedence over the asserted `outage`.
  The independently reviewed correction grants only that authenticated administrator through the
  Add form and verifies the grant in the request, creation response, and later listing while
  retaining Alice/Bob denials. A new hosted run, both complete final-image journeys, and matched
  docker-box deployment/rollback remain pending; T89 stays In Progress.
- 2026-09-23: The fourth hosted
  [CI run 35821247681](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35821247681)
  at exact PR head `8abc8be6a2b2585686d612bce328b2070a14b99a` passed both Python shards,
  unconditional PostgreSQL/S3 coverage, and aggregate coverage at 90.30% application branches
  (7,370/8,162) and 90.64% changed lines (3,670/4,049). Both rootless-image profiles passed the
  connection browser test and full real Composer routing, TLS, draft, and revision journey. The
  standalone profile then failed opening `/login` immediately after forcing the fake scanner
  offline, before scanner-outage resilience assertions. The distributed profile passed those
  outage assertions but failed the mapped scanner readiness probe after scanner restart. An
  isolated existing-image Podman reproduction established that a dynamically allocated scanner
  IP changes across stop/start while the harness pins the original IP in dependent containers.
  A one-file harness correction now assigns a network-derived static scanner IP and adds bounded
  status-only router/frontend/backend probes to diagnose the separate standalone `/login`
  failure; it retains the real scanner stop/start, fail-closed upload, retained-read, and mapped
  recovery checks. Local shell checks and 38 harness tests pass. Independent review, hosted
  rerun, both complete final-image profiles, and docker-box deployment/rollback remain pending.
  T89 stays In Progress.
- 2026-09-23: The fifth hosted
  [CI run 35823925638](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35823925638)
  at exact PR head `ec28dcfcf0a6c8ff81cbd36ce9a8196bec6f6584` completed with the complete
  distributed rootless-image E2E workflow passing, including real Composer connections,
  TLS/model calls, durable revisions, scanner-offline fail-closed upload with retained owner
  reads, resumed scanner, isolated backup/restore, and restart checks. The standalone image
  again passed the full real Composer journey but stopped before scanner-outage assertions:
  one-shot probes after scanner stop showed the production router `/login` and the `frontend`
  network alias timing out, while the same frontend page at its inspected numeric address and
  backend readiness both returned 200. The light job separately failed when the 50-card
  pagination test exceeded Vitest's five-second default by 64 ms; 250 other web tests passed.
  All other substantive jobs passed, so the aggregate gate failed only for light and standalone
  E2E. Independently reviewed local corrections use the current validated numeric frontend
  address inside the E2E production router and give only the 50-card test a ten-second bound;
  they retain real browser-to-router routing and all scanner/revision assertions. The next hosted
  run must pass both profiles and the light job; matched docker-box deployment/rollback remains
  pending. T89 stays In Progress.
- 2026-10-11: Completed and verified on `main`. Every acceptance criterion is evidenced:
  - Connection permissions, identity modes, model discovery/selection/test, write-only
    credentials, mTLS and internal CA, destination/TLS policy, redaction, bounded calls, safe
    errors, browser configuration, durable drafts/revisions in both profiles, egress separation,
    readiness states, API/CLI parity, migrations, and English guides were delivered by PR #267
    (`8b6dc3af46f5c45da46cdd1a50c4676cd8e8d7e0`); the Administration hub, standalone LLM setup,
    ETag-fenced policy, envelope-key identity checks, bounded policy audit, and private
    document-engine network namespaces by PR #268 (`9b1828dd159dfcfe58ee8ca1ee87a485a0bffd29`).
    Both received independent review. PR CI runs
    35826556272 and 35865408040 (attempt 2) and main CI runs 35829579577 and 35871945081
    (attempt 2) passed, including Python coverage, `container`, and both final rootless-image
    E2E profiles.
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
  - Real LiteLLM and provider qualification remains assigned to T93, as recorded on 2026-09-23
    and in T93's acceptance criteria; it is not a T89 criterion.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-588.
The administrator setup correction was authorized while Linear access was unavailable on
2026-09-23; synchronize this updated acceptance criterion and progress with G1L-588 when access
returns. T89 remains In Progress until its remaining qualification and deployment criteria pass.

- 2026-09-26: Linear access returned. G1L-588 retains the full acceptance description and now
  records the verified main merges of T89 foundation PR #267, administrator setup PR #268, and
  T90 PR #269. Status and priority remain In Progress and High. The matched docker-box test-instance
  deployment and rollback evidence are still unverified, so no completion is claimed.
