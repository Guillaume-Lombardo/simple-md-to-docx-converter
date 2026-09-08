---
ticket: T54
linear_id: G1L-461
linear_url: https://linear.app/g1lom/issue/G1L-461/t54-publish-the-post-t38-cli-entrypoint-release-and-atomically-pin
status: Done
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

* 2026-08-31: Phase 2 PR [#148](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/148) was squash-merged as `0384d605890ffb2c9d3120c98b3558004a0af8e1`. Exact-main CI run `33429139713` passed the light, container, Compose, standalone E2E, distributed E2E, and gate jobs. Public Compose and every quickstart now use the supported 0.5.0 CLI roles and immutable GHCR digest, so all T54 acceptance criteria are verified on `main`.

* 2026-08-31: Phase 1 was squash-merged by PR #147 as `34d2460d1e43c3c5ffcdd80b67d8188b98cb80cb`. Automatic release run `33414896392` completed successfully: PyPI published `markweave 0.5.0`, the lightweight `v0.5.0` tag and GitHub Release resolve to that exact source SHA, and GHCR publication, SBOM/vulnerability evidence, provenance attestation, and retained release evidence all passed. The retained receipt records registry digest `sha256:a00767265c6c35b3fb19c4464e04d9f507940415a8c277e28f5bdc0f7dc420a4`; a separate anonymous manifest fetch returned the same digest and verified it against the manifest bytes. Phase 2 adopts that exact digest, uses the supported `serve` Compose role, removes the obsolete public legacy-environment bridge, and validates the public quickstarts plus both final-image profiles. Two consecutive cold Docker quickstart runs exposed a deterministic 15-second LibreOffice template-validation timeout, so the bounded local-evaluation timeout is raised to 30 seconds before repeating the complete workflow. The complete Docker quickstart then passed, including conversion, restart, recovery, and fault scenarios. The final-image E2E runner also accepts a strictly validated immutable published-image override without changing its default source-build CI path. Both standalone and distributed final-image workflows passed against exact image `0.5.0@sha256:a00767265c6c35b3fb19c4464e04d9f507940415a8c277e28f5bdc0f7dc420a4`, including security boundaries, service and CLI conversion, browser provisioning, restart recovery, checkpoint verification, remote-client entrypoints, and login-origin enforcement.

* 2026-08-31: The complete pull-request container build detected a mutable UBI repository inventory change. A local rebuild and an exact comparison against the published `0.4.0` final image confirmed the same 549 packages with one maintenance update only: `tar` moved from `1.34-11.el9` to `1.34-13.el9_8` with the same GPLv3+ license. The reviewed final-image inventory hash is now `3c4d1883b398ebf8b2bdaa3e5fb9ff956214e395b6517e00f1e58f0903a49576`, and the full local image build passed with that hash.

* 2026-08-31: Phase 1 now bumps the reviewed package and application version to `0.5.0`, resets the release attempt to `1`, and records the release changelog. The read-only CI gate verifies fixed public PyPI, GitHub tag/Release receipt, and GHCR endpoints with bounded injectable HTTP behavior. Normal revisions require full project/PyPI/Compose/tag/receipt/digest alignment; only the exact `0.4.0` base to higher `0.5.0` transition is accepted on pull-request, merge-group, and trusted push events. The phase also performs the approved bounded Compose catch-up from stale `0.3.5` to the already-published immutable `0.4.0@sha256:f1dacb99881d9890efc34ba8327afc23b0c9b1ed7f713876e35e04b36bbb6ab3` image, retaining `embedded-worker`. The public checker passed against the real `0.4.0` PyPI, GitHub, and GHCR evidence. Phase 2 remains responsible for adopting the actual published `0.5.0` receipt digest, migrating Compose and quickstarts to `markweave serve` and `markweave worker`, and validating both profiles.

* 2026-08-31: The product manager selected version `0.5.0` and approved the existing two-pull-request release sequence. Phase 1 owns the protected version transition and durable CI enforcement of PyPI, GitHub Release, GHCR, and Compose alignment. Phase 2 starts only after publication evidence exists, adopts the exact immutable GHCR digest, migrates public Compose and quickstarts to `markweave serve` and `markweave worker`, and validates both profiles. The unavoidable publication-to-pin interval must be explicit and fail closed rather than silently leaving Compose stale.

* 2026-08-31: Created after the product manager selected the two-stage release-and-pin sequence. T54 exclusively owns the published public Compose and quickstart migration; T38 is independently completable after its source-built entrypoint change is verified on `main`.
* 2026-08-31: T38 implementation PR [#140](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/140) was squash-merged as `47e34da52d2c1782c2dc6006e83060d796f5127e`, and exact-main CI run `33379357180` passed the light, container, Compose, both-profile E2E, and gate jobs. The source-built entrypoint prerequisite is satisfied.

## Coordination

* Status: Done.
* T38's source-built implementation dependency is verified on `main`, and the selected release version is `0.5.0`.
* T54 is the explicit owner of version/release-attempt metadata and immutable public image pinning for this release only.
* After phase 1 merges, suspend unrelated integrations, monitor automatic publication to a terminal result, and immediately start phase 2 from the exact retained publication receipt when it succeeds.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
