---
ticket: T89
linear_id: G1L-588
linear_url: https://linear.app/g1lom/issue/G1L-588/t89-implement-secure-composer-connections-and-durable-revision
status: Backlog
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

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-588.
