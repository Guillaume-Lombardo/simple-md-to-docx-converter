---
ticket: T87
linear_id: G1L-586
linear_url: https://linear.app/g1lom/issue/G1L-586/t87-validate-documentation-and-publish-patch-release-072
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T87 - Validate documentation and publish patch release 0.7.2

## Objective

After the approved priority work and documentation review are verified, publish Markweave patch release 0.7.2 and adopt its exact public image receipts.

## Acceptance criteria

- Verify and correct user, API, CLI, configuration, recovery and release documentation against delivered behavior; preserve historical evidence identities.
- Complete the T83 inventory concurrency correction, independent review and final-image qualification before release.
- Bump authoritative package/application metadata from 0.7.1 to 0.7.2, reset release attempt to 1, update the lockfile, OpenAPI version and changelog through existing tooling.
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
Public deployment pins remain on 0.7.1 until verified 0.7.2 publication receipts exist.
