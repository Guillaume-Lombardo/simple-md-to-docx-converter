---
ticket: T46
linear_id: G1L-421
linear_url: https://linear.app/g1lom/issue/G1L-421/t46-add-security-reporting-and-support-policies
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T46 - Add security reporting and support policies

## Objective

Publish clear security-reporting, supported-version, response, disclosure, and operational-support policies consistent with Markweave's threat model.

## Acceptance criteria

* Add `SECURITY.md` with private reporting instructions that do not request secrets or hostile document contents through public issues.
* Define supported release lines, security update expectations, disclosure coordination, dependency/container triage, and scope boundaries.
* Document where deployment, configuration, backup, and usage support belongs and what information can be shared safely.
* Link the policies from README, PyPI metadata, documentation index, and repository community surfaces where applicable.
* Validate links and ensure all content is English, actionable, and consistent with the product specification.

## Dependencies

* T22
* T23

## Implementation boundary

* Own `SECURITY.md`, support policy content, and their links/metadata.
* Do not change runtime security behavior or release versioning.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
