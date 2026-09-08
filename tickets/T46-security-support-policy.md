---
ticket: T46
linear_id: G1L-421
linear_url: https://linear.app/g1lom/issue/G1L-421/t46-add-security-reporting-and-support-policies
status: Done
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

* 2026-09-08: PR [#220](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/220) passed independent review, CodeRabbit, and exact-head CI run `34198740596`, then squash-merged as `3038b069e7c4f3ddd21859bbd72abe9fee9bbbf3`. Exact-main CI run `34199987764` passed at that SHA; all acceptance criteria are verified with no remaining T46 limitation.
* 2026-09-08: Non-draft pull request [#220](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/220) was published from reviewed commit `aee2afeae27c14d83c87a87c0871e14336c1ab26`; exact-head CI and CodeRabbit review are pending.
* 2026-09-08: Resumed by product-manager decision after GitHub private vulnerability reporting and Discussions were enabled. G1L-421 and this mirror are In Progress.
* 2026-09-08: Reconciled normally with exact `main` `38ed74bdcd6692ae9235cc2f2190f7af4dab041e`; the preserved policy files required no conflict resolution. Refreshed `SUPPORT.md` so the T40-owned PyPI `Support` URL lands on the documented question channel while reproducible defects remain in GitHub Issues.
* 2026-09-08: Verified the enabled private-reporting and Discussions settings through the GitHub API, all dedicated-policy local link targets, public repository/Issues/Discussions responses, and the unchanged PyPI `Support` metadata URL. `uv sync --all-groups`, Ruff format/lint, `ty`, and `git diff --check` pass. A proportional canonical Pytest run was stopped after 9m10s at 43%; PostgreSQL errors and S3 failures require the unavailable `MARKWEAVE_TEST_POSTGRES_URL` and `MARKWEAVE_TEST_S3_*` services, while one unrelated process-reaping test failed. Hosted exact-head CI remains the full-suite authority.
* 2026-09-03: Deferred by the product manager. The completed policy implementation remains preserved on `docs/T46-security-support-policy`; resume only when private vulnerability reporting and a usable support channel are authorized.
* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up serialized policy links after T40 and removed package metadata from T46 ownership.
* 2026-08-29: Final audit follow-up moved shared documentation navigation exclusively to T50.
* 2026-08-30: Started implementation on `docs/T46-security-support-policy` from `c1cae3b6ca1d2f8eb6e680eec26f444ea92332c5`; G1L-421 is In Progress. This ticket exclusively owns `SECURITY.md`, `SUPPORT.md`, and this mirror.
* 2026-08-30: Added the dedicated private vulnerability-reporting, supported-release, coordinated-disclosure, dependency/container-triage, deployment, backup, and safe-information policies. Dedicated policy links resolve; T40's existing PyPI `Support` metadata URL was verified read-only. Ruff format/lint and `ty` pass.
* 2026-08-30: Reconciled normally with exact `main` `7850ab695ec278012b3db6e00a854b1c9dcf2360`; the merge integrated only the T24 and T27 ticket mirrors. On the merged tree, Ruff format/lint and `ty` pass. The canonical non-engine Pytest command completed with 1,937 passed, 44 deselected, 3 failed, and 32 errors in 13m40s at 95.42% coverage; all failures/errors require unavailable RustFS/S3 or PostgreSQL environment variables (`MARKWEAVE_TEST_S3_*` and `MARKWEAVE_TEST_POSTGRES_URL`), not T46 policy content.
* 2026-08-30: Blocked by repository settings: GitHub private vulnerability reporting is disabled, so the private advisory reporting form is unusable; GitHub Discussions is also disabled, so the `SECURITY.md` fallback and T40-owned PyPI `Support` URL are unusable. Decision needed: the product manager must authorize enabling both GitHub features, or approve another actionable private reporting and support channel plus a separately owned package-metadata correction. T46 must not change repository settings or publish a personal email.

## Coordination

* Status: Done after exact-main verification.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
