---
ticket: T50
linear_id: G1L-425
linear_url: https://linear.app/g1lom/issue/G1L-425/t50-complete-cross-surface-cli-and-package-acceptance
status: Backlog
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

* 2026-09-21: The user approved removing T48 from the blocking dependencies. T50 can qualify
  the delivered package, CLI, containers, and existing mutation baseline independently of T48's
  remaining repository-owned CI integration. All other acceptance criteria and dependencies remain;
  T50 remains Backlog and continues to block T73 until its qualification is verified.

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Final audit follow-up made T50 the exclusive integration owner for shared documentation navigation.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
