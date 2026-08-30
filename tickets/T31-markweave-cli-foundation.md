---
ticket: T31
linear_id: G1L-406
linear_url: https://linear.app/g1lom/issue/G1L-406/t31-build-the-markweave-cli-foundation
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T31 - Build the Markweave CLI foundation

## Objective

Provide the installed `markweave` executable and a stable command framework shared by local operational commands and remote HTTP client commands.

## Acceptance criteria

* Declare a `markweave` console entry point while preserving the package import and `python -m markweave.runtime` compatibility during migration.
* Define stable command groups, help, version output, exit codes, stdout/stderr rules, JSON and human-readable output, timeouts, and non-interactive behavior.
* Keep password and secret values out of arguments, logs, tracebacks, shell completion, and process listings.
* Expose reusable typed command, output, configuration-profile, and error abstractions without importing every optional backend eagerly.
* Own the root registry and pre-register stable placeholder modules for authentication, conversions/jobs, templates, administration/audit/health, runtime operations at `src/markweave/cli/commands/runtime.py`, and recovery operations at `src/markweave/cli/commands/recovery.py` so downstream workers never edit the registry or shared help snapshots.
* Add unit, packaging, clean-wheel installation, and shell-invocation tests; document the CLI contract for downstream tickets.

## Dependencies

* T01
* T06
* T22

## Implementation boundary

* Own the new CLI framework, root registry, initial `pyproject.toml` console entry point, common output/errors, shared help snapshots, command-family placeholders, and CLI contract test helpers.
* After this ticket merges, T40 exclusively owns `pyproject.toml` and release-install verification; T32–T37 exclusively fill their pre-registered family modules and domain tests.
* Do not implement HTTP authentication, resource commands, operational commands, container changes, or configuration renaming.

## Progress

* 2026-08-30: Completed in pull request #104 and squash-merged to `main` as `23abb36832a3d8358700b2d97eee66e75d7fd077`. The exact-main CI run `33299385896` passed every implemented domain and its final gate; CodeRabbit's packaging finding was resolved before merge and the independent reviewer approved the final head.
* 2026-08-30: Addressed independent review findings: package import is now lazy so CLI help/version avoid the server and optional backends; error detail rendering omits untrusted details; and clean-wheel verification executes the generated console script for both version and help. Focused tests, package/release integration, `uv sync --all-groups`, Ruff formatting/linting, and `ty check` passed. The canonical Pytest commands still cannot pass in this environment because PostgreSQL/RustFS and the document engines are unavailable.
* 2026-08-30: Implemented the console entry point, root registry, typed command/output/profile/error contracts, pre-registered unavailable family modules, CLI contract documentation, help snapshot, shell invocation coverage, and clean-wheel entry-point verification. `uv sync --all-groups`, Ruff formatting/linting, `ty check`, targeted CLI tests, and the clean-wheel release integration passed. Both canonical Pytest commands exercised this scope but remain red because this environment lacks Pandoc, Mermaid/Chromium, LibreOffice, PostgreSQL, and RustFS; no T31 failure was observed.
* 2026-08-30: Started implementation on `feat/T31-cli-foundation` from verified `main` at `381e74e9`; this workstream exclusively owns the CLI framework, root registry, shared help contract, command-family placeholders, initial console entry point, and its tests and documentation.
* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up assigned the root registry, shared help snapshots, and pre-registered command-family placeholders exclusively to T31 so parallel CLI workers do not edit shared files.
* 2026-08-29: Final audit follow-up split operational registration into an exclusive T36 runtime family and T37 recovery family.

## Coordination

* Status: Done.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
