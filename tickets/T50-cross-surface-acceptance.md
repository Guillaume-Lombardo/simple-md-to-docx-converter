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
* Verify OpenAPI compatibility, configuration aliases, optional dependencies, resource-warning enforcement, mutation domains, namespace cleanliness, and documentation links.
* Record exact commands, artifacts, skipped prerequisites, residual limitations, and independent review; do not select or publish a release version without explicit product-manager approval.
* Update the product specification, README, documentation index, and ticket evidence so autonomous workers can verify the complete delivered contract.

## Dependencies

* T38
* T41
* T42
* T43
* T44
* T45
* T46
* T47
* T48
* T49

## Implementation boundary

* Own final cross-surface acceptance, evidence, integration documentation, and residual-gap reporting.
* Do not absorb unfinished implementation from dependencies or select/publish a release version without explicit approval.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
