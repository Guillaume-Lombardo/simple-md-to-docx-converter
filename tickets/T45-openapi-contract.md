---
ticket: T45
linear_id: G1L-422
linear_url: https://linear.app/g1lom/issue/G1L-422/t45-version-and-validate-the-openapi-contract
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T45 - Version and validate the OpenAPI contract

## Objective

Commit a deterministic OpenAPI artifact and block accidental incompatible HTTP changes while keeping the generated application schema authoritative.

## Acceptance criteria

* Generate and commit a canonical OpenAPI document from a deterministic application configuration with no secrets or environment-specific values.
* Add CI validation that regeneration is clean and classifies or rejects incompatible route, method, schema, status, header, security, and required-field changes.
* Document the review and intentional-breaking-change workflow and keep runtime `/openapi.json` byte-equivalent after canonical normalization.
* Use the artifact for CLI contract tests or generated fixtures without making generated client code the application source of truth.
* Cover optional template behavior, authentication restrictions, pagination, ETags, errors, downloads, health, and administration routes.

## Dependencies

* T41
* T06

## Implementation boundary

* Own canonical OpenAPI generation, artifact validation, CI integration, and contract documentation.
* Do not change HTTP behavior merely to make a snapshot pass.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-30: Implementation started from `main` at `c1cae3b6ca1d2f8eb6e680eec26f444ea92332c5` on `feat/T45-openapi-contract`.
* 2026-08-30: Added the deterministic `openapi/v1.json` artifact, infrastructure-free generation, runtime and T41 normalized-equivalence tests, compatibility classification and rejection, CLI route coverage, CI enforcement, ETag documentation, and the versioning/review guide. Formatting, lint, types, browser tests, CI policy validation, 1,729 unit tests, and 100% changed application coverage passed. The canonical default and full suites otherwise reached 1,949 and 1,956 passing tests respectively, but could not complete PostgreSQL/RustFS suites without their test environment; the full suite also lacked the pinned Pandoc, Mermaid/Chromium, LibreOffice, and font runtime.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
