---
ticket: T116
linear_id: G1L-645
linear_url: https://linear.app/g1lom/issue/G1L-645/t116-add-signed-job-completion-webhooks
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T116 - Add signed job-completion webhooks

## Objective

Notify external systems when a conversion or reverse-conversion job reaches a terminal state.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- Owners register HTTPS webhook endpoints, subject to an administrator destination allowlist.
- Deliveries are HMAC-signed, content-free (job identifiers and states only), retried with bounded backoff and recorded durably in both storage profiles.
- A dedicated dispatcher performs egress with SSRF protections; document engines remain network-isolated.
- Security, integration and final-image E2E tests.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification
- T114 - Add scoped revocable API tokens

## Blocking points

- Requires decision D9 (outbound network policy).
- Endpoint registration for automation relies on T114 tokens.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-645.
