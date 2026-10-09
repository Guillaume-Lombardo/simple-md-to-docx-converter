---
ticket: T115
linear_id: G1L-640
linear_url: https://linear.app/g1lom/issue/G1L-640/t115-add-oidc-single-sign-on-with-group-to-role-mapping
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T115 - Add OIDC single sign-on with group-to-role mapping

## Objective

Let organizations authenticate through their identity provider using OIDC, mapping IdP groups to Markweave roles, through the existing OIDC-ready abstraction.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- Authorization code flow with PKCE, issuer/audience/nonce validation and configurable group claim mapping to user and administrator roles.
- Local accounts can coexist or be disabled by configuration; the administrator break-glass path is documented.
- CLI login works with OIDC (device authorization flow or documented alternative).
- Integration tests against a pinned disposable IdP container and final-image E2E in both profiles, including clock skew, invalid tokens and removed group membership.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Requires decision D8.
- Integration and E2E tests need a pinned IdP image (for example Keycloak or Dex) in CI.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-640.
