---
ticket: T93
linear_id: G1L-592
linear_url: https://linear.app/g1lom/issue/G1L-592/t93-qualify-composer-and-publish-the-minor-release-with-verified-image
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T93 - Qualify Composer and publish the minor release with verified image receipts

## Objective

Complete Composer acceptance, publish the next minor release (`0.8.0` from the current `0.7.3`
baseline), adopt its exact public image receipts, and deploy the matched release.

## Acceptance criteria

- Trace every requested requirement to implemented evidence and independently review security,
  permissions, secrets, certificates, model failures, invalid responses, concurrency, revisions,
  preservation, and long files in both storage profiles and exact final rootless images.
- Verify with the user-authorized real LLM endpoint and a model selected from its actual catalog
  without exposing secrets; synthetic providers alone do not satisfy provider acceptance. Keep
  docker-box updated for each qualified iteration with rollback evidence.
- Complete English documentation, migration/backup/recovery/operations, API/CLI parity, and measured
  limits. Keep visual comparison, preview section selection, and ONLYOFFICE deferred.
- Bump minor version only after prerequisite acceptance. Use a protected PR, independent review,
  and required checks; monitor automatic PyPI and the complete matched GHCR image set through
  terminal publication success.
- Immediately open a follow-up PR adopting exact published registry receipt digests/SHAs in
  relevant Compose, quickstart, and documentation locations. Verify anonymous public access and
  deploy the matched published set. Never pre-pin unpublished digests or rebuild published bytes.
- Verify main, synchronize Linear, clean up only exact owned branches, and report publication,
  deployment, and any residual limitations.

## Dependencies

T92.

## Progress

- 2026-09-22: Created in Linear and mirrored before implementation. No release or deployment is
  claimed.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-592.
