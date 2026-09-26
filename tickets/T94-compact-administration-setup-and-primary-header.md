---
ticket: T94
linear_id: G1L-593
linear_url: https://linear.app/g1lom/issue/G1L-593/t94-compact-administration-setup-and-primary-header
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T94 - Compact administration setup and primary header

## Objective

Make the existing Next.js administration setup and primary header more compact while preserving
all user-management and conversion/Composer workflows.

## Acceptance criteria

- Integrate existing administrator Users management into the Administration setup hub as an
  accessible collapsible section alongside the existing setup sections. Preserve account search,
  creation, activation/deactivation, password reset, required-renewal controls, session-policy
  access, and administrator authorization.
- Keep the desktop identity controls (user name, role, sign out) on one compact header row, with
  responsive behavior, keyboard access, and clear accessible names.
- Present Convert and Composer as a compact, unambiguous selector near the product name. Preserve
  the active state, routes, availability and permission states, unsent inputs and drafts, and the
  independent quick-conversion workflows. The final visual control awaits the owner's preference
  among underlined tabs, segmented control, and dropdown; use compact underlined tabs if no
  preference is supplied.
- Keep existing deep links and backend authorization/session enforcement unchanged. This is a
  frontend presentation change, not a recovery or deployment fix.
- Add focused component and browser regressions for expansion, navigation, identity controls,
  permissions, and responsive behavior. Run applicable frontend and final rootless-image checks
  in both profiles, reporting unavailable gates explicitly.

## Baselines and dependencies

- T63: Next.js administration.
- T81: navigation labels.
- T90: Composer selector implementation. Its relevant source is merged; live deployment
  acceptance is tracked separately.

## Progress

- 2026-09-26: User authorized this UI scope. Implementation starts on
  `fix/T94-compact-admin-header` from merged main. The selector style remains open pending owner
  preference; the implementation default is compact underlined tabs. No recovery-verifier
  correction or docker-box deployment is in scope. T94 is In Progress.
- 2026-09-26: Implemented the default compact underlined Convert/Composer tabs and a compact
  desktop identity row with name, role, and sign out, retaining responsive wrap. The Administration
  hub now has collapsible setup sections with lazily embedded Users and nested Session policy;
  the top-level Users navigation item is removed while `/users` and `/session-policy` deep links
  remain valid. Focused component tests pass 50/50 and final focused tests pass 16/16. The full
  `pnpm --dir web check` passes 699/699 tests with 90.02% web branch coverage (3,710/4,121),
  plus formatting, lint, types, bindings, and structure checks. Node syntax and Git diff checks
  pass; independent code review found no blocking defects. Rendered browser geometry at desktop
  and mobile widths, both complete final-image E2E profiles, hosted checks, and main verification
  remain pending. T94 stays In Progress; no completion or deployment is claimed.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-593.
