---
ticket: T39
linear_id: G1L-417
linear_url: https://linear.app/g1lom/issue/G1L-417/t39-migrate-configuration-to-markweave-names
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T39 - Migrate configuration to MARKWEAVE names

## Objective

Adopt the Markweave brand for application configuration while preserving deterministic compatibility with legacy `MD_CONVERTER_*` names throughout 0.x.

## Acceptance criteria

* Introduce documented `MARKWEAVE_*` names for every public application setting, runtime host/port, cookie default, and applicable operator surface.
* Accept legacy `MD_CONVERTER_*` aliases for all 0.x releases; parse both definitions independently through the same field validator and compare canonical typed values, except that secrets and opaque tokens compare exact raw strings; unequal definitions fail closed without exposing values.
* Prefer only `MARKWEAVE_*` in new documentation, examples, Compose, quickstarts, tests, and emitted diagnostics; clearly mark legacy names deprecated until 1.0.
* Preserve secret handling, case rules, profile validation, existing deployments, and deterministic precedence for equal dual definitions.
* Preserve the existing `md_converter_session` and `__Host-md_converter_csrf` cookie defaults throughout 0.x so the environment-prefix migration does not silently revoke sessions; document their explicit 1.0 migration boundary.
* Keep the existing `ghcr.io/guillaume-lombardo/md-converter` image name throughout 0.x; an image rename requires a separate dual-publication migration and is not implicit in this ticket.
* Test every alias class, semantically equal typed spellings, raw secret conflicts, cookie compatibility, container/Compose propagation, and upgrade behavior.

## Dependencies

* T06
* T20
* T26

## Implementation boundary

* Own settings aliases, public environment names, Compose/quickstart propagation, and configuration migration documentation.
* Do not implement CLI commands, dependency extras, or unrelated runtime refactors.

## Progress

* 2026-08-30: Implemented the canonical `MARKWEAVE_*` environment surface with deprecated 0.x
  `MD_CONVERTER_*` aliases. Startup independently validates both aliases, compares typed canonical
  values (or exact raw secrets), and fails closed without values on conflicts. Updated Compose,
  quickstarts, deployment examples, container and CI surfaces, documentation, and compatibility
  tests. Preserved `md_converter_session`, `__Host-md_converter_csrf`, and
  `ghcr.io/guillaume-lombardo/md-converter`; validated targeted configuration, documentation,
  quickstart, and Compose-rendering tests.
* 2026-08-30: Removed baked canonical host/port image environment defaults so legacy-only upgraded
  deployments do not acquire accidental dual definitions. The final-image API smoke now launches a
  complete legacy-only configuration with `MD_CONVERTER_HOST=127.0.0.1` and
  `MD_CONVERTER_PORT=18080`, verifies live/ready endpoints from inside the container namespace,
  and checks the resolved settings before continuing with the canonical workflow.
* 2026-08-30: Diagnosed ready-CI Compose job `99227321692`: the pinned published 0.3.5 quickstart
  image predates T39 and therefore rejects a canonical-only environment. Added a tested equal-value
  legacy bridge generated exclusively from the canonical Compose inputs; both the pinned image and
  T39-capable upgraded images start safely without operator-managed legacy variables.
* 2026-08-30: Extended the trusted-upstream overlay bridge with
  `MD_CONVERTER_MALWARE_SCANNING_MODE=trusted-upstream`. Rendered Compose validates both aliases
  through current settings, and the actual pinned 0.3.5 trusted-upstream quickstart started healthy
  without a ClamAV container.
* 2026-08-30: Started implementation on `feat/T39-configuration-names` from verified `main` at `381e74e9`; this workstream exclusively owns settings aliases, public environment names, Compose/quickstart propagation, compatibility tests, and configuration migration documentation.
* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up fixed typed alias comparison and preserved 0.x cookie and GHCR identities explicitly.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
