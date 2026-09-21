---
ticket: T86
linear_id: G1L-585
linear_url: https://linear.app/g1lom/issue/G1L-585/t86-improve-ci-image-downloads-and-verify-dependency-updates
status: Backlog
priority: High
project: Markdown to DOCX and PDF Converter
---

# T86 - Improve CI image downloads and verify dependency updates

## Objective

Diagnose PR #248's network failure, verify its grouped dependency updates and make CI image acquisition resilient to transient transport failures without weakening immutable pins or test gates.

## Acceptance criteria

* Record the precise failing command and upstream error from PR #248 and distinguish infrastructure failure from dependency/test regressions.
* Review the three dependency updates, lockfile consistency, relevant support/security evidence and compatibility checks; record the outcome of the existing dependency PR without duplicating its changes blindly.
* Add a small bounded retry mechanism at the failing image-acquisition boundary with actionable logs and deterministic final failure. Preserve exact digest pins, integrity verification and existing timeout/resource limits; do not retry test failures or use mutable fallback images.
* Cover transient success, retry exhaustion, and permanent/integrity failure handling with appropriate harness/policy tests, and verify the affected distributed rootless E2E path on the exact PR head.
* Review other pending dependency updates and report actionable issues without broad unsolicited upgrades.
* Run applicable canonical checks and independent review, preserve coverage/gates and verify main after merge.

## Dependencies

* T22, T27 (delivered baselines).

## Implementation boundary

Own image-acquisition reliability in CI/container harnesses and focused policy tests. Coordinate CI workflow ownership with independent T48 mutation integration and later T73 final-image work. Do not change branch protection or publish a product release.

## Progress

* 2026-09-21: Created from user priority order 4. PR #248 at 8e8bdbde59c03a963e9b0f789120fc69d63c706f failed distributed E2E with unexpected EOF downloading a UBI9 Python 3.14 image blob from Quay (exit 125). Other matrix jobs passed; this observation does not yet establish dependency compatibility.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
