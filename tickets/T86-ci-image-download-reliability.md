---
ticket: T86
linear_id: G1L-585
linear_url: https://linear.app/g1lom/issue/G1L-585/t86-improve-ci-image-downloads-and-verify-dependency-updates
status: In Progress
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

Own bounded transient acquisition retries for immutable CI/container images, checksum-pinned Containerfile downloads, and read-only public-release alignment HTTP requests, with focused policy tests. Coordinate CI workflow ownership with independent T48 mutation integration and later T73 final-image work. Do not change branch protection or publish a product release.

## Progress

* 2026-09-21: Created from user priority order 4. PR #248 at 8e8bdbde59c03a963e9b0f789120fc69d63c706f failed distributed E2E with unexpected EOF downloading a UBI9 Python 3.14 image blob from Quay (exit 125). Other matrix jobs passed; this observation does not yet establish dependency compatibility.
* 2026-09-21: Linear moved to In Progress. The exact failing command was the unguarded `podman pull --quiet "$base_image"` in `scripts/e2e/run.sh`, after the distributed E2E workflow workload had completed. Podman exited 125 while reading Quay's `fbe94d...` UBI9 Python 3.14 layer from the upstream S3 URL with `unexpected EOF`; the other 15 CI jobs passed. This is an infrastructure transport failure, not a dependency or test regression.
* 2026-09-21: Reviewed PR #248's only three updates: `boto3` 1.43.95 to 1.43.97, `hatchling` 1.32.0 to 1.32.3, and `ty` 0.0.81 to 0.0.82. `uv lock --check` passes and the complete PR matrix passed except for the isolated Quay transport failure. The locked Boto3 release supports Python 3.14; the Hatchling release is not yanked and has PyPI attestation; Ty publishes an immutable signed release with attestations. No other open dependency PR exists. Dependabot security alerts are disabled, so alert review could not be performed without administrative configuration.
* 2026-09-21: Added a three-attempt, two-second, immutable-image acquisition helper for final-image CI and locally built rootless E2E. It retries only classified transport failures, never prints captured upstream URLs, rejects a permanent or integrity error before retrying, and rechecks the exact digest after a successful pull. Focused policy tests cover transient success with a presigned-URL-shaped EOF fixture, retry exhaustion, and permanent/integrity no-retry behavior. Formatting, lint, type, lock, shell-syntax, and focused policy checks pass. The exact PR #248 distributed E2E rerun remains pending coordinated CI capacity.
* 2026-09-21: User approved a bounded scope extension after three independent infrastructure failures: PR #248 distributed E2E reached a GitHub-release Pandoc download that returned HTTP 504 during the Containerfile build; PR #254 reached a pinned UV download that returned HTTP 504; and PR #254 public alignment timed out fetching the GitHub Release receipt. The extension covers only classified transient retries at these checksum-pinned download and read-only public-release boundaries; checksum, signature, digest, redirect, authorization, and not-found failures remain fail-closed and non-retried.
* 2026-09-21: Added bounded Curl retries to all checksum-pinned Containerfile downloads without `--retry-all-errors`; each transfer keeps HTTPS restrictions, a 20-second connect timeout, a 180-second transfer timeout, three attempts, and a 120-second retry budget before its existing checksum/signature validation. Public alignment now retries only 429/5xx responses, timeouts, and connection resets, preserving response-byte limits, trusted redirects, and terminal authentication/not-found/TLS-certificate behavior. Focused policy checks cover retry exhaustion and now pass (118 selected tests), together with formatting, lint, type, lock, and shell-syntax checks. Exact CI reruns and post-merge main validation remain pending.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
