---
ticket: T67
linear_id: G1L-533
linear_url: https://linear.app/g1lom/issue/G1L-533/t67-migrate-javascript-tooling-to-a-pnpm-workspace
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T67 - Migrate JavaScript tooling to a pnpm workspace

## Objective

Migrate the repository's browser-test and Next.js JavaScript tooling from two independent npm installations to one root pnpm workspace with a single deterministic lockfile, without changing product behavior or weakening release, container, or test evidence.

## Acceptance criteria

* Add a root pnpm workspace whose membership patterns explicitly include only the root browser-test package and `web/` and explicitly exclude `spikes/toolchain`; add an automated negative membership test that fails if the isolated package is ever resolved by the root workspace. Select, document, and enforce exact reviewed pnpm and Corepack versions on developer machines, GitHub Actions, and container builders.
* Generate one canonical root `pnpm-lock.yaml` and remove `package-lock.json` plus `web/package-lock.json` only after dependency versions, peer resolution, overrides, integrity metadata, scripts policy, generated bindings, builds, runtime contents, and test behavior have demonstrated parity.
* Keep `spikes/toolchain/package-lock.json` and its isolated npm-based Mermaid production graph unchanged unless a separately reviewed expansion proves equivalent publisher-integrity, SBOM, license, vulnerability, container, and rollback evidence. Preserve and execute `npm ci --prefix spikes/toolchain --omit=dev --ignore-scripts` plus the existing exact Mermaid version, lock-integrity, and container checks.
* Replace npm commands and caches across applicable package scripts, GitHub Actions, the frontend Containerfile, rootless E2E setup, CI domain selection/validation, maintenance automation, and developer/operator documentation with frozen, non-interactive pnpm equivalents; retain `--ignore-scripts` or a stricter explicit allowlist.
* Make workspace commands unambiguous and preserve the existing root browser-module tests, frontend formatting/lint/type generation/type checking, OpenAPI binding freshness, unit/component coverage, production build/runtime tests, and deterministic generated-file checks.
* Preserve reproducible frontend image construction: install from the reviewed lock without mutation, produce an exact pruned production dependency graph, keep package-manager tooling and caches out of the runtime image, and retain arbitrary-UID, read-only-root, resource-bound, SBOM, provenance, vulnerability, and license behavior.
* Treat Corepack and pnpm bootstrap artifacts as supply-chain inputs: pin and verify their exact source and integrity, fail closed on version mismatch or unavailable verified bytes, prohibit mutable tags and implicit network activation, and retain dependency audit, license, SBOM, and provenance evidence.
* Key GitHub caches by operating system, Node version, exact pnpm version, and lockfile content; prevent cache writes from untrusted contexts and prove clean cold-cache execution.
* Record comparable GitHub-hosted cold install, warm-cache install, cache size, workspace `node_modules`/store disk use, frontend build time, and final frontend image size for the npm baseline and pnpm candidate, with runner image, Node version, commands, sample count, and raw evidence. Any material regression requires explicit approval rather than an invented threshold.
* Run the complete ready-PR and merge-queue branch gate in GitHub, including frontend, functional, document-engine, both storage-profile, and both rootless final-image E2E jobs; run a clean exact-main verification after merge before completion.
* Update the normative package-manager decision and affected architecture, local-development, CI, container, deployment, maintenance, and rollback documentation only after T64 releases ownership of those files.
* Rehearse a rollback that removes `pnpm-workspace.yaml`, `pnpm-lock.yaml`, pnpm/Corepack manager-selection and configuration state, pnpm cache configuration, and every pnpm CI, container, script, and documentation command while restoring both npm application/tooling lockfiles, npm package-manager metadata, npm caches, and the frontend build from one coherent reviewed commit. Prove clean `npm ci --ignore-scripts` at the root and clean `npm ci --prefix web --ignore-scripts` with no mixed npm/pnpm state, lockfile drift, implicit lock update, dynamically selected package-manager version, or runtime network installation.

## Dependencies

* T64

## Implementation boundary

* Own the root JavaScript workspace/package metadata, root and `web/` application/tooling lockfiles, pnpm/Corepack bootstrap, applicable CI/cache/domain-selection tests, frontend image installation, rootless E2E dependency setup, and package-manager documentation after T64.
* Do not change FastAPI or frontend product behavior, generated OpenAPI bindings, dependency versions, the isolated Mermaid toolchain lock, or T64-owned files while T64 is active. Any dependency refresh or Mermaid-toolchain migration requires separately reviewed scope.

## Quality requirements

* Preserve exact dependency and security policy parity before deleting either npm application/tooling lockfile.
* Keep all repository artifacts and user-facing text in English.
* Require independent review and full hosted GitHub evidence because local execution cannot establish cache service, merge-queue, image publication, or both-profile E2E parity.

## Progress

* 2026-09-03: Created in Backlog as an independent post-T64 migration. Inventory found separate root and `web/` npm installations plus an intentionally isolated npm-locked Mermaid production toolchain; implementation must preserve that boundary unless scope is explicitly expanded.
* 2026-09-03: Implemented the root pnpm workspace candidate with integrity-bound Corepack 0.36.0 and pnpm 11.25.0 bootstrap, a single frozen lock retaining the exact npm-baseline package/version set, explicit `spikes/toolchain` exclusion, root-context frontend construction with a portable production deployment graph, trust-scoped caches, rootless E2E setup, and historical npm release-lock recovery. Local browser-module, frontend binding, structure, coverage, build, production-runtime, rootless image, dependency-audit, isolated Mermaid npm-lock, CI-policy, and targeted Python tests pass. The final local frontend image is 1,033,797,849 bytes versus 1,061,525,142 for npm; an earlier 2,705,797,855-byte cross-platform deployment experiment was rejected and removed. The host provides Node 24.18.1 rather than the required 24.19.0, and PostgreSQL/S3 services are not configured, so exact-host-runtime, complete canonical, hosted cold/warm benchmark, ready-PR/merge-queue, and rollback-rehearsal evidence remains pending. Normative specification reconciliation is deferred until the active T69 owner releases `docs/product-specification.md`.
* 2026-09-03: Merged T69 planning main `521e9eea4fb7c628263d9c1dce68dccbecdf5e6a` without rewriting history and made the validated T67 workspace, bootstrap, cache, container, isolated-toolchain, and historical-release-lock contract normative. The implementation is ready for independent review; hosted benchmark, complete ready-PR/merge-queue, and post-merge exact-main evidence remain required before the ticket can be marked Done.
* 2026-09-03: Strengthened the rollback rehearsal after independent review. The harness now binds the exact T67 candidate series to the audited npm parent, rejects unrelated first-parent commits and unowned paths, constructs the coherent rollback by reversing only T67 patches, byte-compares every T67-owned surface with the npm baseline except the intentionally retained merged T69 specification delta, and fails if npm locks, metadata, cache policy, commands, frontend build context, production prune, E2E setup, or the isolated Mermaid install are not restored or any pnpm/Corepack state remains. Ready pull requests run this rehearsal in the frontend domain on exact Node/npm versions. Documentation now consistently describes workspace-scoped frontend checks and runtime package-manager exclusion. Hosted cold/warm benchmark, full ready-PR/merge-queue, and post-merge exact-main evidence remain pending.
* 2026-09-03: Completed the deterministic rollback-state rehearsal against the committed T67 candidate and corrected its CI lifetime after follow-up review. The hosted invocation is restricted to a pull request whose exact head branch is `chore/T67-pnpm-workspace` in this repository, so merge-queue, main, forks, and later frontend pull requests cannot execute the candidate-specific history check. Local post-commit tests prove the coherent reverse patch and npm command sequence with exact tool-version stubs; the hosted Node.js 24.19.0/npm 11.17.0 execution, cold/warm benchmark, complete ready-PR and merge-queue matrix, and post-merge exact-main verification remain required.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, or progress changes.
