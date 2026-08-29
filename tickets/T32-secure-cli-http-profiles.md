---
ticket: T32
linear_id: G1L-407
linear_url: https://linear.app/g1lom/issue/G1L-407/t32-add-secure-http-login-and-cli-profiles
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T32 - Add secure HTTP login and CLI profiles

## Objective

Authenticate remote CLI commands through the existing session and CSRF contract and store bounded connection profiles safely.

## Acceptance criteria

* Implement `markweave login`, `logout`, and `whoami` against the documented HTTP API without adding API tokens.
* Prompt passwords through a non-echoing terminal path and reject password arguments and environment persistence.
* Store URL, opaque session state, and CSRF state under the XDG directories with owner-only `0600` files and safe atomic replacement; never store the password.
* Support named profiles, explicit profile selection, TLS verification by default, session expiration, forced password renewal, and deterministic non-interactive failures.
* Test file permissions, symlink/path attacks, hostile profile data, CSRF renewal, redaction, and real HTTP behavior.

## Dependencies

* T31
* T06
* T30

## Implementation boundary

* Own CLI HTTP transport, XDG profiles, session/CSRF handling, and login/logout/whoami commands.
* Do not add API tokens, change server authentication semantics, or implement resource commands.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.

