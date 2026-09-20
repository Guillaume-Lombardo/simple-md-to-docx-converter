---
ticket: T81
linear_id: G1L-580
linear_url: https://linear.app/g1lom/issue/G1L-580/t81-shorten-workflow-tab-labels
status: In Progress
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T81 - Shorten workflow tab labels

## Objective

Rename the browser tabs to `2docx`, `2md`, and `templates` as requested on 2026-09-20.

## Acceptance criteria

- Update visible and accessible navigation labels, including disabled states.
- Preserve routes, permissions, active states, independent workflows, and the Experimental badge.
- Update existing component and browser E2E selectors and current user documentation.
- Run relevant frontend checks and record any validation gaps.
- Display the release version as a small subscript beside Markweave in the primary
  navigation; derive it from `pyproject.toml` for local and container builds.

## Dependencies

- T76 (completed navigation baseline; original T16/T17 workflow scope).

## Progress

- 2026-09-20: Refined the version position to a second row centered below `eave`.
  Frontend check passes (220 tests), production build passes, and sandboxed Chromium
  verifies vertical separation and centering against both local production output
  and deployed HTTPS assets using synthetic API responses. Deployed image:
  `sha256:24be1d2a5e4ad884b7ecf135c226fe5628922aa7763df68903c40c6c62173b02`.
  Four services healthy; HTTPS readiness/login pass. Remote runbook contains rollback
  via `compose.t81.pre-logo.yaml`. This does not complete or deploy T82.

- 2026-09-20: User-authorized docker-box update completed. Frontend image
  `sha256:77cbe1c0986d17f24a6cad00ad8570dad693c85ebe87c9a24a7d0671db002109`
  was built from archive `3a9e5540d5c5fd9027f9258a28d4eb3b3c9e861af2a7ef97fbfbe397cd10e4d8`
  with the repository Containerfile. It delivers the version subscript, `2docx`
  naming and expired recent-list filtering. All four services are healthy, broker
  active, public HTTPS readiness/login pass, and served navigation JavaScript
  confirms labels and `Version 0.6.4`. Remote DEPLOYMENT.md records rollback via
  `compose.t81.pre-version.yaml`. Authenticated workflows were not rerun;
  unfinished T82 backend changes remain local. No Git publication or merge.

- 2026-09-20: Added an accessible release-version subscript beside Markweave.
  Next.js derives the public build constant from `pyproject.toml`; the Containerfile
  supplies the same metadata to build and runtime. No manual duplicate or version bump.
  Validation passes: frontend check (220 tests, nine structure tests, 90.40% branch
  coverage), production build, five production tests, 16 browser-helper tests,
  E2E script syntax and `git diff --check`. Initial test-environment and pre-hydration
  assertions were corrected; all rerun checks pass. The final-image authenticated
  browser assertion was added but not run. Python suites and Docker build were not
  rerun for this frontend-only addition; docker-box has not received this change.

- 2026-09-20: Authorized test deployment completed at https://markweave.g1lom.xyz.
  SSH used the retained deployment-specific known_hosts file and forwarded agent.
  The original frontend Containerfile built successfully on docker-box from source archive
  `06673bdd690e74396cc8411da9c1c798a885f61a994fa70be3375be3ffe11ff1`.
  Compose pins the test frontend by local image ID
  `sha256:3b643cd475f21cd3631e324fb1b8917073817184cb8a6126fa88693c2b4703fc`.
  All four services are healthy and the broker is active. Public HTTPS readiness/login
  and the actual served JavaScript confirm the new labels and retained Experimental text.
  Authenticated browser workflows were not rerun. The remote runbook records the exact
  source, image, verification and rollback. No Git publication or main merge occurred.
- 2026-09-20: User authorized deployment to the docker-box test instance. SSH to both
  `docker-box` and its Tailscale FQDN fails host-key verification before authentication.
  No remote state was changed. Deployment requires verified SSH trust/access from codex-dev.
- 2026-09-20: Implementation started. This task does not include a version bump or publication.
- 2026-09-20: Updated visible/accessible and disabled tab labels, existing component and E2E selectors, current documentation, specification, and unreleased changelog. Routes, permissions, and Experimental badge are preserved.
- Validation passed: frozen pnpm installation; `pnpm --filter @markweave/web check` (format, lint, types, bindings, 9 structure tests, 207 component tests; 90.32% branch coverage); `pnpm run test:web` (16 tests); `uv sync --all-groups`; `uv run ruff format --check .`; `uv run ruff check .`; `uv run ty check`; `uv run pytest tests/test_quickstart_compose.py --no-cov` (47 tests); `git diff --check`.
- Validation limits: canonical filtered/full Python suites and final-image E2E runs were not executed for this label-only change. E2E selectors are updated but final-image verification remains pending. No push, PR, release, or deployment performed; status remains In Progress until verification on main.
