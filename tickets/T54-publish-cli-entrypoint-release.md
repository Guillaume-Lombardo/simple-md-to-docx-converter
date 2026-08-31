---
ticket: T54
linear_id: G1L-461
linear_url: https://linear.app/g1lom/issue/G1L-461/t54-publish-the-post-t38-cli-entrypoint-release-and-atomically-pin
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T54 - Publish the post-T38 CLI-entrypoint release and atomically pin Compose/quickstarts

## Objective

Publish the first post-T38 release whose final image exposes the Markweave CLI entrypoint, then atomically pin public Compose and quickstart workflows to that immutable image.

## Acceptance criteria

* Obtain an explicit product decision for the release version before changing version metadata; do not infer or choose the version.
* For this release only, own the coordinated `pyproject.toml` version and `tool.markweave.release.attempt` update needed by the release workflow.
* Publish the post-T38 image through the existing T22 release workflow and verify its immutable digest, provenance, signature, and installed `markweave` entrypoint.
* In the same reviewed change that adopts the published digest, migrate every public Compose role and public quickstart workflow to `markweave serve` and `markweave worker`; never point public instructions at an unpublished or mutable image.
* Run standalone and distributed quickstart/final-image validation against the exact published digest, including representative operational and remote-client commands.
* Record the exact version and digest in Linear and the repository ticket mirror.

## Dependencies

* T38
* T22
* T40

## Implementation boundary

* Exclusively own the selected release's version/release-attempt metadata, publication evidence, immutable public Compose pin, public quickstart command migration, and their focused tests.
* Do not choose the version, modify unrelated CLI internals, or absorb changelog/upgrade/shared-navigation work owned by T47 and T50.
* Do not extend T38's completion boundary: this ticket starts only after T38's source-built entrypoint migration is verified on `main`.

## Progress

* 2026-08-31: Created after the product manager selected the two-stage release-and-pin sequence. T54 exclusively owns the published public Compose and quickstart migration; T38 is independently completable after its source-built entrypoint change is verified on `main`. Version selection remains an explicit pending product decision.

## Coordination

* Status: Backlog.
* T54 is the explicit owner of version/release-attempt metadata and immutable public image pinning for this release only.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
