---
ticket: T89
linear_id: G1L-588
linear_url: https://linear.app/g1lom/issue/G1L-588/t89-implement-secure-composer-connections-and-durable-revision
status: In Progress
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

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-588.
