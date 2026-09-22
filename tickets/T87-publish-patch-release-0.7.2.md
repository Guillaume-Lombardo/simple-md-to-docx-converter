---
ticket: T87
linear_id: G1L-586
linear_url: https://linear.app/g1lom/issue/G1L-586/t87-validate-documentation-and-publish-patch-release-072
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T87 - Validate documentation and publish patch release 0.7.3

## Objective

After the approved priority work and documentation review are verified, publish Markweave patch release 0.7.3 after the failed 0.7.2 container qualification, and adopt its exact public image receipts.

## Acceptance criteria

- Verify and correct user, API, CLI, configuration, recovery and release documentation against delivered behavior; preserve historical evidence identities.
- Complete the T83 inventory concurrency correction, independent review and final-image qualification before release.
- Preserve immutable 0.7.2 PyPI/tag history; bump authoritative metadata from 0.7.2 to 0.7.3 with release attempt 1, updating the lockfile, OpenAPI version and changelog through existing tooling.
- Correct the T73 exact-attempt fault-injection selection and synchronization with regression coverage and independent review. Admit only the strictly verified failed-container 0.7.2-to-0.7.3 transition; retain the 0.7.1 pins until actual new receipts exist.
- Publish through the existing protected-main automatic workflow only. Verify terminal success for PyPI, GitHub Release and all three matched backend/frontend/reverse-attempt images, with exact source, receipts, scans and provenance.
- Immediately adopt exact published registry digests in the relevant Compose, quickstart and reverse-broker deployment documentation and configuration. Never pre-pin an unpublished digest or rebuild published bytes.
- Verify public anonymous access, installation and applicable published-image quickstart acceptance for both storage profiles; preserve the external broker isolation contract.
- Keep the docker-box test instance current with qualified results and retain rollback evidence.
- Complete independent review, required checks, exact-main verification, ticket synchronization and exact source-branch cleanup. T73 becomes Done only when its public reverse-image criterion is verified; T74 remains deferred.

## Dependencies

- T22 (release infrastructure, complete)
- T83 (feature and final qualification)

T87 consumes T73's technical evidence after the T83 inventory correction is requalified, and
delivers its remaining public-image criterion. T73 completion is an outcome, not a prerequisite.

## Scope and authorization

The user explicitly requested final documentation validation followed by a patch bump and the full yolo sequence through image publication and cleanup on 2026-09-22, then explicitly authorized modifications. No Kubernetes, OCR, protection bypass, force push or unrelated infrastructure change.

## Progress

2026-09-22: Documentation and release-readiness audits started while the authorized PR #259 inventory snapshot correction is implemented. No version change or public publication has occurred.

2026-09-22: PR #259 merged as `41329266140ce0a49a74cc8eab559e07a03df605` after successful
exact-head CI `35713604587`, independent review and 4,698 passing local tests. Prepare the reversible
release branch from that main commit while main CI `35716730560` runs. Publication remains gated on
terminal successful prerequisite main CI and the release PR's own checks; no main qualification or
public publication is claimed early.

2026-09-22: Prerequisite main CI `35716730560` completed successfully at
`41329266140ce0a49a74cc8eab559e07a03df605`. Independent version and documentation reviews
approved the release preparation. The 177 focused release/version/OpenAPI tests and 159
documentation/policy tests passed, as did static checks and exact version-transition gates.
Public deployment pins remain on 0.7.1 until verified 0.7.3 publication receipts exist.

2026-09-22: PR #260 merged as `b28256486af4cf6d58c3aa06a27815c321df57f9`; both PR CI
`35719688632` and main CI `35723245238` passed. Automatic release `35723245369` published
PyPI and tag/Release 0.7.2 at that source; public isolated Python 3.14 import and CLI checks passed.
All three images built and passed supply-chain checks. Standalone release qualification passed,
but distributed broker-restart qualification rejected a paused unit that did not match its synthetic
recovery target. The harness selected any new matching worker/policy unit before validating the
exact target. The strict guard correctly failed closed. The precise interleaving is unproven.
Registry publication and staging-artifact retention were skipped; only the Python artifact remains.

The user explicitly authorized the targeted harness correction, its independent review and
regressions, and publication of 0.7.3 through deployment and cleanup. A narrowly verified failed
0.7.2 container transition is required; immutable PyPI/tag history is preserved. No 0.7.2 image
rebuild or protection bypass is authorized. Implementation resumes on `fix/T87-release-0.7.3`.

The corrected observer validates the exact synthetic first-attempt identity before selecting a
runtime candidate. The harness waits for the diagnostic observer's first successful database query
before releasing work, preserving signed inventory, runtime incarnation and cleanup checks.
The focused harness suite passes 71 tests, including real SQLite readiness/binding and negative
cases for another unit, delayed diagnostics, a missing target and a later attempt. Removing only
the exact-attempt filter makes the competing-unit regression fail; restoring it passes.
The bounded release-alignment exception passes 105 tests and live verification of the six absent
GHCR tags. Its code and documentation are independently approved. Version/OpenAPI/clean-install
checks pass 81 tests, and documentation/changelog checks pass 38. Global formatting, lint, typing,
lockfile and dependency checks pass. Full qualification and public 0.7.3 publication remain pending.

2026-09-22: PR #261 CI `35731906682` attempt 2 passed image acquisition and the primary
API, CLI, corpus and structured-PPTX paths, then failed while pausing the exact recovery target
in the distributed lifecycle harness. Target completion before pause is plausible but unproven;
target-state evidence was absent. One authorized network retry was consumed. The remaining
network retry does not cover this failure.

The user authorized preparing runtime verification before observer readiness, publishing a readable
binding directly from the diagnostic process, and retaining bounded content-free target-state
evidence. Regression tests, independent review and actual local qualification of both profiles must
precede resumed publication. Isolation, signed identity, exact-attempt selection and cleanup guards
remain required. Version 0.7.3, deployment and cleanup remain pending.

The follow-up harness correction passes 77 focused unit/integration tests, including preflight
failure without readiness, permission failure without binding publication, exited or absent target
state, and preservation of the original pause error when diagnostic writing fails. Global Ruff and
typing checks pass. Current 0.7.3 application images were built from `6be1533`; the later harness
changes do not change image inputs. Independent review approved the correction and reproduced all
77 passing tests; local profile qualification remains pending. A disk-capacity failure during the reverse-image build was resolved by explicitly
authorized removal of the three obsolete local `e3fb99b` candidates after live verification of their
archived OCI checksums on docker-box. The active `4888cd0` images and rollback evidence remain.

Full local qualification at `d562aab` passed both profiles against the matched `6be1533` application
images. Both runs verified worker and broker crash recovery, frontend outage, persisted termination
evidence, structured PPTX API/CLI/browser workflows and recovered result downloads. The original
distributed run failed before current-image testing because the historical 0.5.2 rollback fixture
was locally mode 0700. An exact-image arbitrary-UID probe confirmed it was not executable. After
explicitly authorized restoration of that single local file to 0755, the complete distributed rerun
passed. File contents and Git state were unchanged; original failed evidence is retained. Both
successful profiles retain the fake-ClamAV teardown warning rather than claiming a graceful stop.

PR #261 merged as `84e35fed521c61d34823d18767f47eb87253d4eb` after CI `35742704086`
passed all 18 jobs, independent review and CodeRabbit. Main CI `35747016968` and automatic
release `35747017469` both completed successfully. Public PyPI 0.7.3 installation, import, version
and CLI help passed independently under Python 3.14. The release qualified both full profiles before
publishing the same backend, frontend and reverse-attempt image bytes with attestations.

Schema-2 receipt checksums and anonymous version/source-tag manifests agree for all three roles.
The adoption branch updates Compose and quickstart defaults to those exact published backend and
frontend receipts; the reverse image is recorded for the separately configured external broker.
Full archive verification, published quickstart checks, docker-box deployment and adoption merge
remain required before completion. Historical 0.7.2 publication and failed qualification evidence
are preserved. The exact PR #261 branch was deleted locally and remotely after verified merge.
