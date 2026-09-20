---
ticket: T84
linear_id: G1L-583
linear_url: https://linear.app/g1lom/issue/G1L-583/t84-decompose-oversized-application-python-modules-by-responsibility
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T84 - Decompose oversized application Python modules by responsibility

## Objective

Continue T79 for the eight remaining Python modules above 1,000 lines under `src`,
using explicit names and cohesive responsibilities without changing behavior.
Tests and scripts are outside the size-reduction scope.

## Acceptance criteria

- Decompose broker inventory, Podman/Kubernetes runtime, Unix/mTLS/node-attester
  transports, reverse-job SQL persistence, and the private anydoc compatibility
  boundary; leave no application Python module above 1,000 physical lines.
- Preserve public imports, protocol bytes, authentication, transaction scopes,
  lock ordering, lease/proof fences, runtime containment and deterministic output.
- Keep one private anydoc compatibility package and parse path, the pinned engine,
  private-symbol inventory and license; introduce no parser or renderer features.
- Run focused contract/security/parity tests, applicable canonical checks and both
  SQL profiles; report environment and final-image validation gaps accurately.
- Update the responsibility inventory and preserve coverage gates. Do not change
  dependencies, test/script organization or deployments. The user additionally
  authorized the 0.7.1 patch release, reviewed publication and merge, monitoring
  PyPI and paired GHCR publication, published-digest adoption and documentary closure.

## Dependencies

- T79 (completed)
- Preserve T05, T08, T12, T13, T18, T20, T21 and T70/T71/T74 contracts.

## Progress

- 2026-09-20: Started at the user's request. Inventoried the eight remaining modules;
  implementation and validation pending. Linear G1L-583 is In Progress.
- 2026-09-20: Implemented eight-to-24 responsibility decomposition on
  `refactor/T84-application-module-responsibilities`; every `src` Python file is
  below 1,000 lines (maximum 989). The public import paths and anydoc supply-chain
  license location remain unchanged. Only two test files needed mock-target or
  package-relative license-path adjustments; no test/script size refactoring.
- AST comparison verified 416 of 419 original functions unchanged. The other
  three delegate to extracted Pod/Podman functions with identical bodies after
  substituting explicit configuration parameters. SQL transactions, inventory
  transitions, proof sequencing and renderer algorithms remain intact.
- Initial focused validation: 1,141 passed and two cgroup mock-target failures;
  corrected the targets and all 151 Podman tests passed. Ruff format/lint and `ty`
  pass. `uv build` produced the sdist and wheel; all 217 application modules and
  the unchanged license are packaged, and all 17 new module paths import in
  separate fresh Python processes. Canonical default validation is running.
- 2026-09-20: Final canonical default suite passed: **4,397 passed, 56 engine-marked
  deselected, 11 warnings** in 25m49s, including real broker/Unix/mTLS/Podman,
  anydoc parity, SQLite, PostgreSQL and S3 integration. Overall coverage is
  **94.94%**, application branch coverage **91.17% (6,400/7,020)**, and changed
  executable-line coverage **95.23% (2,773/2,912)**. Changed-line measurement uses
  the existing CI calculator against the working diff plus every untracked new
  Python module, so extracted files are included before commit.
- Final `uv sync --all-groups`, `uv run ruff format .`,
  `uv run ruff format --check .`, `uv run ruff check .`, `uv run ty check`,
  `uv build`, and `git diff --check` passed. Intermediate import cycles,
  unused-import/type errors and stale cgroup mocks were corrected before the
  passing canonical run. Direct file invocation of the branch-coverage helper
  could not import `scripts`; its supported module invocation
  `uv run python -m scripts.ci.check_branch_coverage --coverage coverage.json`
  passed. No disposable test-service containers, managed Podman attempts or
  task-owned tmux session remain.
- The unrestricted `uv run pytest` command was not run because host Pandoc,
  Mermaid/Chromium and LibreOffice executables are unavailable. Final-image E2E
  and hosted CI were not rerun in this local-only task; no acceptance exception
  or publication is claimed. Changes remain uncommitted on the working branch.
  T84 remains In Progress until a reviewed merge is verified on main.

- 2026-09-20: User authorized the 0.7.1 patch release through `$yolo`, publication
  monitoring and documentary closure. Public quickstart pins remain at 0.7.0
  until the exact 0.7.1 registry receipts are published.

- Release preparation: version 0.7.1 is synchronized across package metadata,
  lockfile, runtime and OpenAPI snapshots. Release/version/OpenAPI tests passed
  (242); documentation/quickstart tests passed (56). Ruff, ty, locked dependency
  validation, canonical CI validation and OpenAPI compatibility checks passed.
  Public pins are unchanged pending publication; hosted final-image CI and
  independent review remain required before merge.

- PR #247 merged as `5dd3328a17d12279d1c7c11a8c96b68cb062ecb3` after all
  17 hosted CI checks and independent CodeRabbit review passed. Hosted coverage:
  90.10% branches and 94.78% changed application lines. Main CI `35537766132`
  subsequently passed, including both final-image E2E and storage profiles.
- Automatic release `35537766223` completed successfully: PyPI 0.7.1, both GHCR
  images, SBOMs, receipts and provenance are published. Public PyPI installation
  and tag source verified. Receipt identities, all 13 attached release-manifest
  entries, bundle evidence checksums and frontend lockfile verified.
- The immediate adoption follow-up on `chore/T84-adopt-0.7.1-images` pins the
  exact published backend/frontend registry digests in Compose, both quickstarts
  and their E2E assertions, and updates release/deployment documentation. No
  quickstart-command migration is needed. Remains In Progress until adoption is
  verified on main and the documentary mirror is closed.

- Adoption validation: anonymous public alignment passes; 96 documentation,
  quickstart and release-alignment tests pass. The real published-pair rootless
  Podman quickstart E2E passed, including exact running image digests, routing,
  host validation, teardown/restart and state preservation. Disposable services
  and volumes were cleaned up. Ruff format/lint, ty and diff checks pass.

## Synchronization

Keep Linear and this mirror synchronized. Mark Done only after verification on main.
