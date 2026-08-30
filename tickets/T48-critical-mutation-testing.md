---
ticket: T48
linear_id: G1L-424
linear_url: https://linear.app/g1lom/issue/G1L-424/t48-expand-mutation-testing-across-critical-invariants
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T48 - Expand mutation testing across critical invariants

## Objective

Extend bounded mutation testing from observability to the security, authentication, archive, queue, worker, and persistence invariants most likely to fail silently.

## Acceptance criteria

* Define a reviewed risk-ranked mutation scope and deterministic per-domain commands with bounded runtime; the gate passes only when every selected non-equivalent mutant is killed, while each excluded/equivalent mutant requires a reviewed technical justification recorded in the artifact.
* Cover authentication/session versioning, origin/CSRF checks, archive/path/SVG validation, job leases/fencing/idempotency, retention, and storage integrity.
* Add or strengthen tests to kill relevant surviving mutants without asserting implementation trivia.
* Run affected mutation domains on pull requests and a broader bounded matrix on schedule, preserving one required gate and useful artifacts.
* Fail on surviving, untested, timed-out, or suspicious selected mutants; document reviewed exclusions with technical justification and track the exact killed/selected counts without weakening existing coverage thresholds.

## Dependencies

* T41
* T42
* T43
* T05
* T22

## Implementation boundary

* Own mutation configuration, CI scheduling, artifacts, and mutation-driven tests after target modules stabilize.
* Do not combine production refactors with mutation-test enablement.

## Progress

* 2026-08-31: Reconciled `chore/T48-critical-mutation-testing` with verified `main` SHA `7cf98f5f548288447276024aa8f4ae9f613e2cd7` through a normal merge, preserving the T33, T35, and T53 changes. Ruff formatting/linting, `ty check`, and the 390-test targeted suite pass. A fresh real campaign killed all 21 selected mutants, with every strict failure status at zero. `main` currently requires only `CI / gate`; it does not require the workflow check `Mutation / critical gate`, so T48's required-gate acceptance criterion remains blocked pending separately authorized branch-protection change. PR #106 was inspected read-only and remains open/draft and untouched.
* 2026-08-30: Implemented the reviewed four-domain campaign with 21 exact mutants, pull-request affected-domain selection, the full scheduled/manual campaign, one stable read-only gate, and always-retained JSON evidence. A fresh real mutmut 3.7.0 run killed all 21 selected mutants with every failure status at zero. Mutation-driven tests now lock session/authentication/CSRF, archive/SVG, request identity/idempotency, lease recovery/fencing, retention, and filesystem storage behavior; the frozen-slots origin dependency that mutmut cannot transform is documented in the evidence with its direct functional/integration coverage retained. No production code was refactored.
* 2026-08-30: Verification passed `uv sync --all-groups`, Ruff formatting and linting, `ty check`, the real 21/21 mutation campaign, CI policy validation, and a 327-test targeted unit/integration suite. The canonical engine-excluded suite reached 1,999 passing tests and 95.68% coverage; its only failures were 3 RustFS tests and 32 PostgreSQL setup errors because `MARKWEAVE_TEST_S3_ENDPOINT_URL` and `MARKWEAVE_TEST_POSTGRES_URL` are not configured. Pandoc, Mermaid/Chromium, and LibreOffice are also unavailable locally, so the external-engine/full-suite paths remain for CI.
* 2026-08-30: Started implementation on `chore/T48-critical-mutation-testing` from exact verified `main` SHA `8f3b792ec41b10467c771c543a292929b0fa985a`. T05, T22, T41, T42, and T43 are all `Done`; scope remains limited to risk-ranked mutation configuration, CI scheduling and evidence, and mutation-driven tests without production refactoring.
* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up made zero surviving selected non-equivalent mutants the deterministic gate.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
