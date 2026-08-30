---
ticket: T47
linear_id: G1L-419
linear_url: https://linear.app/g1lom/issue/G1L-419/t47-add-changelog-and-upgrade-compatibility-guidance
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T47 - Add changelog and upgrade compatibility guidance

## Objective

Provide concise release history and a deterministic operator upgrade/rollback contract across package, container, configuration, and database changes.

## Acceptance criteria

* Add a maintained changelog focused on user-visible, operational, security, deprecation, and compatibility changes rather than ticket implementation detail.
* Document supported upgrade paths, schema migration ordering, backup prerequisites, rollback limitations, container/package version alignment, and 0.x compatibility policy.
* Document `MD_CONVERTER_*` to `MARKWEAVE_*` migration and removal at 1.0 without inventing other unresolved product values.
* Add `scripts/release/check_changelog.py` and `tests/release/test_check_changelog.py` so a material version transition cannot omit its entry, without editing T40's release-install verifier.
* Create stable changelog and upgrade-guide link targets; T50 owns links from README, deployment, configuration, recovery, release documentation, and the documentation index.

## Dependencies

* T39
* T22
* T23

## Implementation boundary

* Exclusively own `CHANGELOG.md`, `docs/upgrading.md`, `scripts/release/check_changelog.py`, its focused unit and integration tests, and the minimal `.github/workflows/release.yml` detect-job hook plus its CI-policy validator and tests.
* Do not edit README, `docs/index.md`, deployment/configuration/recovery/release guides, T40's release-install verifier, or security/support policy files; T50 owns cross-guide navigation.
* The release-workflow change may invoke the checker after exact transition detection and before remote release checks, but must not alter publication or mutation logic.
* Do not select a release version or implement configuration aliases.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Final audit follow-up assigned shared documentation navigation exclusively to T50 and restricted T47 to dedicated files.
* 2026-08-30: Implementation started on `feat/T47-upgrade-guidance` from verified main `d3c7a2f`. This workstream owns only the changelog, upgrade guide, changelog checker, its tests, and this ticket mirror.
* 2026-08-30: Added release-focused changelog history, deterministic upgrade and rollback guidance, canonical configuration migration guidance, and a tested fail-closed checker for dated material-version entries. Targeted documentation and checker tests, Ruff, and ty passed; repository-wide suites still require unavailable external engines and services.
* 2026-08-30: Independent review clarified that automatic enforcement is inherent in the checker acceptance criterion. T47 therefore owns the minimal release detect-job hook and its policy tests; publication and mutation logic remain out of scope.
* 2026-08-30: Review corrections added the automatic hook before remote release checks, real-Git boundary coverage, Markdown/date fail-closed parsing, explicit upgrade anchors, accurate T39 equal-alias guidance, and updated changelog history. Targeted checker, Git integration, workflow-policy, and documentation tests passed.
* 2026-08-30: Final review corrections reject nested blockquote and list headings and make the stable-link list use the explicit upgrade and configuration anchors.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
