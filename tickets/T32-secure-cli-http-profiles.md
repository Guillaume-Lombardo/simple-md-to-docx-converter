---
ticket: T32
linear_id: G1L-407
linear_url: https://linear.app/g1lom/issue/G1L-407/t32-add-secure-http-login-and-cli-profiles
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T32 - Add secure HTTP login and CLI profiles

## Objective

Authenticate remote CLI commands through the existing session and CSRF contract and store bounded connection profiles safely.

## Acceptance criteria

* Implement `markweave login`, `logout`, `whoami`, and `password change` against the documented HTTP API without adding API tokens.
* Prompt passwords through a non-echoing terminal path and reject password arguments and environment persistence.
* Store URL, opaque session state, and CSRF state under the XDG directories with owner-only `0600` files and safe atomic replacement; never store the password.
* In a restricted renewal session, prompt current password, new password, and confirmation without echo; submit the existing CSRF-protected renewal endpoint, discard the restricted session on success, and require a fresh login.
* Support named profiles, explicit profile selection, TLS verification by default, session expiration, forced password renewal, and deterministic non-interactive failures.
* Test file permissions, symlink/path attacks, hostile profile data, CSRF renewal, password-renewal success and mismatch/failure, redaction, and real HTTP behavior.

## Dependencies

* T31
* T06
* T30

## Implementation boundary

* Own CLI HTTP transport, XDG profiles, session/CSRF handling, and login/logout/whoami/password-change commands inside T31's pre-registered authentication family.
* Do not add API tokens, change server authentication semantics, or implement resource commands.

## Progress

* 2026-08-30: Added the rootless final-image CLI workflow to the existing Podman E2E harness. It drives the installed executable through a PTY, sends the fixture password only after the non-echoing prompt, verifies login/whoami/logout, and asserts non-interactive renewal fails safely.
* 2026-08-30: Implemented the authentication command family on this branch: HTTPS-only standard-library transport, atomic owner-only XDG profiles, non-echoing prompts, login/logout/session inspection, and restricted-session password renewal with a mandatory fresh login. Added unit, shell, and real loopback-HTTP coverage for safe profile handling, path/symlink attacks, hostile state, CSRF, redaction, mismatch, expiration cleanup, and transport serialization. Pending independent review and full repository validation before publication.
* 2026-08-30: Started implementation on `feat/T32-secure-cli-profiles` from verified `main` at `ca3fe44`; this workstream owns the pre-registered authentication command family, HTTP transport, and secure XDG profile storage, and must not modify T39-owned configuration files.
* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up made self-service password renewal an explicit restricted-session CLI workflow.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
