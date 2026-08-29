---
ticket: T38
linear_id: G1L-413
linear_url: https://linear.app/g1lom/issue/G1L-413/t38-use-the-markweave-cli-as-every-container-entrypoint
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T38 - Use the Markweave CLI as every container entrypoint

## Objective

Make the final container expose the same supported `markweave` commands used by package installations and remove container-only runtime invocation paths.

## Acceptance criteria

* Use `markweave serve` and `markweave worker` for standalone and distributed container roles while preserving command override support.
* Ensure doctor, migrate, backup, restore, and HTTP client commands are available in the final image without adding unsafe privileges or writable paths.
* Keep arbitrary-UID, read-only-root, capability, seccomp, signal, shutdown, health, and resource-bound behavior unchanged.
* Update Containerfile, Compose, smoke, quickstart, and executable deployment/recovery flows to call the supported CLI without editing shared documentation navigation.
* Run both-profile final-image E2E for every operational role and representative remote-client commands.

## Dependencies

* T33
* T34
* T35
* T36
* T37
* T40
* T20
* T21

## Implementation boundary

* Own Containerfile, Compose and quickstart manifests, container smoke/E2E, and executable deployment/recovery command wiring.
* Do not edit README, `docs/index.md`, changelog, upgrade guidance, security/support policy, or shared cross-guide links; T50 owns their final integration.
* Treat CLI internals and configuration alias behavior as upstream contracts; do not refactor them here.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Final audit follow-up excluded shared documentation navigation and assigned it to T50.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
