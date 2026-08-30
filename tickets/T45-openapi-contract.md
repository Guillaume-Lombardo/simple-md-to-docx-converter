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
* 2026-08-30: Merged current `main` at `7850ab695ec278012b3db6e00a854b1c9dcf2360` without rebasing; its only intervening changes close the verified T24 and T27 ticket mirrors. Post-merge Ruff, `ty`, 283 focused contract/T41/CI tests, generator freshness and equality, compatibility validation, CI policy validation, and the 1,729-test unit coverage gate passed with 90.30% application branch coverage and 100% changed application coverage.
* 2026-08-30: Resolved review findings by making required-field, constraint, and enum compatibility request/response-directional; adding regressions for real request and response schemas and newly required bodies; and declaring the existing session-cookie security boundary with explicit public operations in generated OpenAPI. Ruff, `ty`, 286 focused contract/T41/CI tests, generator freshness and equality, CI policy validation, and 1,732 unit tests passed with 90.31% application branch coverage and 100% changed application coverage.
* 2026-08-30: Corrected nested-schema compatibility so adding previously absent `items` or `additionalProperties` constraints is classified directionally, with regressions against real request components. Ruff, `ty`, 287 focused contract/T41/CI tests, generator freshness and equality, CI policy validation, and 1,733 unit tests passed with 90.31% application branch coverage and 100% changed application coverage.
* 2026-08-30: Independently approved at `cf58c61bee2b132cccc075759d42b6384fb390ed`, then reconciled current `main` at `3546ce1189d18992316abef1636ac89e247a0a10` through a normal merge. The incoming delta was limited to T49 package-namespace validation and its ticket mirror, with no T45 overlap. Ruff, `ty`, 287 focused contract/T41/CI tests, generator freshness and equality, CI policy validation, all 8 incoming package-namespace tests, and 1,740 unit tests passed with 90.31% application branch coverage and 100% changed application coverage.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
