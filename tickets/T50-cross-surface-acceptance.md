---
ticket: T50
linear_id: G1L-425
linear_url: https://linear.app/g1lom/issue/G1L-425/t50-complete-cross-surface-cli-and-package-acceptance
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T50 - Complete cross-surface CLI and package acceptance

## Objective

Integrate the CLI, package, container, configuration, contract, documentation, and maintainability work into one reproducible release-ready acceptance matrix.

## Acceptance criteria

* Run an acceptance matrix covering package installs, all CLI command groups, standalone and distributed containers, two users and one administrator, document engines, backup/restore, and failure recovery.
* Verify human and JSON CLI contracts, exit codes, authentication-profile security, HTTP-only business operations, operational direct-access boundaries, and container parity.
* Verify OpenAPI compatibility, configuration aliases, optional dependencies, resource-warning enforcement, existing mutation domains, namespace cleanliness, and documentation links. Record remaining T48 gate integration as separate follow-up, not a completion prerequisite; preserve existing mutation tests and coverage thresholds.
* Record exact commands, artifacts, skipped prerequisites, residual limitations, and independent review; do not select or publish a release version without explicit product-manager approval.
* Exclusively update README, `docs/index.md`, and cross-guide navigation for container, recovery, configuration, upgrade, changelog, security, support, and release documentation; update the product specification and ticket evidence so autonomous workers can verify the complete delivered contract.

## Dependencies

* T38
* T41
* T42
* T43
* T44
* T45
* T46
* T47
* T49

## Implementation boundary

* Own final cross-surface acceptance, evidence, integration documentation, residual-gap reporting, README, `docs/index.md`, and every cross-guide navigation edit deferred by T38, T46, and T47.
* Do not absorb unfinished implementation from dependencies or select/publish a release version without explicit approval.
* T48 is not a blocking dependency. Qualify the delivered mutation baseline without taking ownership of T48's remaining CI integration or requiring contributor-immutable enforcement.

## Progress

* 2026-09-21: Verified Done after PR #254 squash merge `6bae1e4d4abc14c44dfca32752bc422d0dfd2502` and successful main CI [35629775995](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35629775995). The reviewed matrix retains exact candidate full-suite evidence (35618280319, attempt 2), 4,400 passing local canonical tests, corrected baseline full-main CI 35626304165, and final PR CI 35626505860. Mutation contracts are qualified; actual T48 campaign execution and its CI integration remain explicitly separate and nonblocking. No release version or publication was selected. T73 may now begin.

* 2026-09-21: Candidate `3ee121a023b9207a72e2c18f0af9003bde69397e` completed the full
  hosted acceptance matrix in CI run
  [35618280319, attempt 2](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35618280319/attempts/2).
  Manual dispatch selected every registered domain with `--full`; light, Python shards and
  coverage, functional, frontend, standalone/distributed storage, document engines, CI
  infrastructure, container, Compose, standalone/distributed final-image E2E, and the final gate
  all passed. The E2Es cover the installed CLI families, two regular users and one administrator,
  authentication boundaries, production backup/restore, restart/readiness failures, checkpoint
  restore, and recovery in both profiles. Independent orchestrator review approved the scoped
  evidence and navigation change.

* 2026-09-21: The exact candidate's canonical engine-excluded suite passed with 4,400 tests, 56
  deselections, and 94.95% coverage. It supplies the clean source/sdist/wheel/editable install,
  installed-shell/import, and real-HTTP CLI integration evidence that is not directly selected by
  a named complete-suite domain; the hosted final-image E2Es independently cover the public HTTP
  CLI workflows. No actual mutation campaign ran at the T50 SHA. The 25/25 killed result with zero
  strict failures and a passing CI mutation job belongs to separate T48 head
  `83ec1b44af15e18fbd1bf896204159208372b106`; T48 remains a nonblocking follow-up.

* 2026-09-21: A subsequent UBI repository change produces a six-package curl/OpenSSL inventory
  mismatch in new source-image builds, including T48's otherwise-passing CI E2E build. The user
  approved the cross-cutting inventory correction owned by T86 PR #255. T50 does not absorb that
  work or make T48 a dependency; integrated-main verification must use the corrected inventory.
  T50 remains In Progress until its evidence change is merged and verified on `main`.

* 2026-09-21: Started cross-surface qualification from `origin/main` at
  `4ef6a53f262a8ca33e34dce2a54a2c28933ed908`. Linear and this mirror are synchronized to
  In Progress. The qualification will record reproducible evidence and honest prerequisite gaps;
  it will not choose a release version or absorb T48/T73 scope.

* 2026-09-21: The user approved removing T48 from the blocking dependencies. T50 can qualify
  the delivered package, CLI, containers, and existing mutation baseline independently of T48's
  remaining repository-owned CI integration. All other acceptance criteria and dependencies remain;
  T50 is now In Progress and continues to block T73 until its qualification is verified.

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Final audit follow-up made T50 the exclusive integration owner for shared documentation navigation.

## Coordination

* Status: Done.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
