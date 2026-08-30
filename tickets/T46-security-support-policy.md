---
ticket: T46
linear_id: G1L-421
linear_url: https://linear.app/g1lom/issue/G1L-421/t46-add-security-reporting-and-support-policies
status: Blocked
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
* Create stable `SECURITY.md` and `SUPPORT.md` policy link targets, and verify the PyPI metadata link already owned and added by T40 without editing `pyproject.toml`.
* Validate links within the dedicated policy files and ensure all content is English, actionable, and consistent with the product specification; T50 owns README, documentation-index, and cross-guide links.

## Dependencies

* T22
* T23
* T40

## Implementation boundary

* Exclusively own `SECURITY.md` and `SUPPORT.md`; do not edit README, `docs/index.md`, cross-guide links, package metadata, or release-install verification.
* Do not change runtime security behavior or release versioning.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up serialized policy links after T40 and removed package metadata from T46 ownership.
* 2026-08-29: Final audit follow-up moved shared documentation navigation exclusively to T50.
* 2026-08-30: Started implementation on `docs/T46-security-support-policy` from `c1cae3b6ca1d2f8eb6e680eec26f444ea92332c5`; G1L-421 is In Progress. This ticket exclusively owns `SECURITY.md`, `SUPPORT.md`, and this mirror.
* 2026-08-30: Added the dedicated private vulnerability-reporting, supported-release, coordinated-disclosure, dependency/container-triage, deployment, backup, and safe-information policies. Dedicated policy links resolve; T40's existing PyPI `Support` metadata URL was verified read-only. Ruff format/lint and `ty` pass.
* 2026-08-30: Reconciled normally with exact `main` `7850ab695ec278012b3db6e00a854b1c9dcf2360`; the merge integrated only the T24 and T27 ticket mirrors. On the merged tree, Ruff format/lint and `ty` pass. The canonical non-engine Pytest command completed with 1,937 passed, 44 deselected, 3 failed, and 32 errors in 13m40s at 95.42% coverage; all failures/errors require unavailable RustFS/S3 or PostgreSQL environment variables (`MARKWEAVE_TEST_S3_*` and `MARKWEAVE_TEST_POSTGRES_URL`), not T46 policy content.
* 2026-08-30: Blocked by repository settings: GitHub private vulnerability reporting is disabled, so the private advisory reporting form is unusable; GitHub Discussions is also disabled, so the `SECURITY.md` fallback and T40-owned PyPI `Support` URL are unusable. Decision needed: the product manager must authorize enabling both GitHub features, or approve another actionable private reporting and support channel plus a separately owned package-metadata correction. T46 must not change repository settings or publish a personal email.

## Coordination

* Status: Blocked.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
