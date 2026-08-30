---
ticket: T49
linear_id: G1L-423
linear_url: https://linear.app/g1lom/issue/G1L-423/t49-remove-legacy-package-artifacts-and-enforce-namespace-cleanliness
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T49 - Remove legacy package artifacts and enforce namespace cleanliness

## Objective

Remove residual `md_converter` build/runtime artifacts and prevent the retired namespace or stale local release outputs from contaminating development and package verification.

## Acceptance criteria

* Remove ignored legacy bytecode trees and obsolete `dist/md_converter-*` artifacts from the maintained workspace using a documented safe cleanup path.
* Ensure clean source, sdist, wheel, editable install, and test environments cannot import the retired `md_converter` namespace.
* Add repository checks that detect unexpected source namespaces, tracked bytecode/build outputs, stale package names, and inconsistent version artifacts.
* Refresh durable orchestration notes to the current verified main state while preserving machine-local notes and unrelated user work.
* Do not remove the legacy environment-variable compatibility required until 1.0 or historical release evidence.

## Dependencies

* T22
* T40

## Implementation boundary

* Own safe legacy-artifact cleanup, a dedicated namespace-check script and dedicated namespace-contamination tests, and durable orchestration-note refresh after T40 finalizes distribution artifacts.
* Do not edit `pyproject.toml`, `scripts/release/verify_install.py`, its tests, package metadata, extras, README, or the documentation index; consume T40's built artifacts as read-only test inputs.
* Preserve historical release evidence and all 0.x legacy environment aliases.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up serialized cleanup after T40 and assigned dedicated namespace checks without shared distribution files.
* 2026-08-30: Started implementation. Added the dedicated namespace checker and clean source, sdist, wheel, and editable-install contamination coverage. Used its dry-run then constrained cleanup to remove only the ignored `src/md_converter` bytecode tree and obsolete `dist/md_converter-0.1.0` artifacts from the maintained checkout; legacy environment compatibility aliases and historical evidence remain untouched.
* 2026-08-30: Reconciled the implementation with main `7850ab695ec278012b3db6e00a854b1c9dcf2360` by a normal merge. Post-merge Ruff, type, and focused namespace checks passed. The full default non-engine Pytest command completed with 1,943 passed, 3 failed, and 32 errors because local PostgreSQL and RustFS services were unavailable; the T49 namespace tests passed in that run.
* 2026-08-30: Tightened review findings: the checker now requires the exact `markweave` project name, and cleanup targets only syntactically valid legacy wheels or source distributions. Regression coverage proves a user file such as `dist/md_converter-customer-backup.gz` is preserved.
* 2026-08-30: Replaced heuristic artifact matching with `packaging` standard wheel and source-distribution filename parsers, requiring the canonical retired distribution name. Cleanup now preserves malformed names including `md_converter-1customer-py3-none-any.whl`, `md_converter-1customer.tar.gz`, and `md_converter-1-customer-py3-none-any.whl`.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
