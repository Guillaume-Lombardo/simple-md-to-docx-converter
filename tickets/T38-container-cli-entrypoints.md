---
ticket: T38
linear_id: G1L-413
linear_url: https://linear.app/g1lom/issue/G1L-413/t38-use-the-markweave-cli-as-every-container-entrypoint
status: Done
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
* Update the Containerfile, source-built deployment/recovery flows, and container smoke/E2E to call the supported CLI without editing shared documentation navigation.
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

* Own the Containerfile, source-built deployment/recovery command wiring, and container smoke/E2E.
* T54 exclusively owns the published public Compose pin and public quickstart command migration after the post-T38 image is released.
* Do not edit README, `docs/index.md`, changelog, upgrade guidance, security/support policy, or shared cross-guide links; T50 owns their final integration.
* Treat CLI internals and configuration alias behavior as upstream contracts; do not refactor them here.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Final audit follow-up excluded shared documentation navigation and assigned it to T50.
* 2026-08-31: Implementation started on `feat/T38-cli-container-entrypoints` from exact `main` at `7cf98f5f548288447276024aa8f4ae9f613e2cd7` after all declared dependencies were verified complete. Scope remains limited to source-built final-image, smoke/E2E, deployment, and recovery invocation wiring; CLI internals and shared documentation navigation remain unchanged.
* 2026-08-31: The published `0.3.5` and `0.4.0` images retain the legacy entrypoint and cannot consume `serve`. The product manager selected a two-stage release-and-pin sequence: T38 completes the source-built entrypoint migration; T54/G1L-461 exclusively owns the post-T38 version/release-attempt update, publication evidence, immutable image digest, and atomic public Compose/quickstart migration. No version has been selected.
* 2026-08-31: The source-built final image now delegates every command to the installed `markweave` program, defaults to `serve`, and uses `worker` for distributed workers. Deployment examples, hardened smoke/recovery flows, and final-image E2E now exercise the supported CLI roles plus representative remote health commands without bypassing the entrypoint. Both full rootless final-image profiles passed against image `43c49ca2ede60e90c0af686beb74fb14a6c48093cbe0f1319b0f2961e46bc610`.
* 2026-08-31: Focused tests, Ruff formatting/lint, ty, and the 23-test browser unit suite pass. The canonical host suites reached 95% coverage but remain environment-limited: the default selection reported 2092 passed, 4 failed, and 32 PostgreSQL setup errors; the full suite reported 2100 passed, 40 failed, and 32 PostgreSQL setup errors because the required PostgreSQL/S3 variables and pinned host document-engine/font environment are unavailable. The same engine and distributed boundaries passed in both final-image E2E profiles.
* 2026-08-31: Implementation PR [#140](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/140) was squash-merged to `main` as `47e34da52d2c1782c2dc6006e83060d796f5127e`. Exact-main CI [run 33379357180](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/33379357180) completed successfully at that SHA, including light, container, Compose, distributed E2E, standalone E2E, and the required gate. This verifies the source-built final-image entrypoint migration on `main`.
* 2026-08-31: T38 is complete within its reviewed boundary. The public `0.3.5` and `0.4.0` image pins still expose the legacy entrypoint; T54 owns the explicit version decision, post-T38 release, immutable digest evidence, and atomic public Compose/quickstart migration. Shared documentation navigation remains owned by T50.

## Coordination

* Status: Done; implementation and exact-main CI are verified at `47e34da52d2c1782c2dc6006e83060d796f5127e`.
* T38's source-built entrypoint dependency for T54 is satisfied. T54 owns the subsequent version decision, release evidence, and public published-image migration.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
