---
ticket: T86
linear_id: G1L-585
linear_url: https://linear.app/g1lom/issue/G1L-585/t86-improve-ci-image-downloads-and-verify-dependency-updates
status: Done
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
* Correct the observed reverse CLI wait deadline race without masking network errors before the deadline or non-network failures, and cover the boundary deterministically.
* Run applicable canonical checks and independent review, preserve coverage/gates and verify main after merge.

## Dependencies

* T22, T27 (delivered baselines).

## Implementation boundary

Own bounded transient acquisition retries for immutable CI/container images, checksum-pinned Containerfile downloads, and read-only public-release alignment HTTP requests, with focused policy tests. Coordinate CI workflow ownership with independent T48 mutation integration and later T73 final-image work. The user also authorized the minimal reverse CLI deadline-race correction required by the failed final-image check in PRs #248 and #253. Do not change branch protection or publish a product release.

## Progress

* 2026-09-22: Completed after main CI `35696095409` succeeded at exact squash
  `cd229f7e8c5779b0d56f2721ba585b5ec3be0210`, including both full E2E profiles,
  document engines, coverage, frontend, storage and the required gate. PR #255 delivered bounded
  acquisition retries and integrity evidence; PR #257 delivered the CLI deadline correction;
  PR #248 delivered the independently reviewed dependency updates. All three are merged and their
  source branches cleaned. Integrity checks, coverage thresholds and branch protections remain
  enforced. Dependency security alerts were unavailable as recorded below; no configuration was
  changed to enable them. No product release was published.

* 2026-09-22: Main `582` rerun `35669415349` attempt 2 succeeded. PR #248 exact head
  `dda91c267f875e54a04ff63b7697d6d26257534a` passed all checks, received independent review, and
  merged as `cd229f7e8c5779b0d56f2721ba585b5ec3be0210`; its local and remote branches were removed
  after verification. PR #248 contains the reviewed Hatchling, Boto3, and Ty dependency updates.
  The CLI deadline correction is PR #257, already merged separately. New main CI
  `35696095409` is still running, so current-main verification is not claimed. T86 remains In
  Progress.

* 2026-09-21: Created from user priority order 4. PR #248 at 8e8bdbde59c03a963e9b0f789120fc69d63c706f failed distributed E2E with unexpected EOF downloading a UBI9 Python 3.14 image blob from Quay (exit 125). Other matrix jobs passed; this observation does not yet establish dependency compatibility.
* 2026-09-21: Linear moved to In Progress. The exact failing command was the unguarded `podman pull --quiet "$base_image"` in `scripts/e2e/run.sh`, after the distributed E2E workflow workload had completed. Podman exited 125 while reading Quay's `fbe94d...` UBI9 Python 3.14 layer from the upstream S3 URL with `unexpected EOF`; the other 15 CI jobs passed. This is an infrastructure transport failure, not a dependency or test regression.
* 2026-09-21: Reviewed PR #248's only three updates: `boto3` 1.43.95 to 1.43.97, `hatchling` 1.32.0 to 1.32.3, and `ty` 0.0.81 to 0.0.82. `uv lock --check` passes and the complete PR matrix passed except for the isolated Quay transport failure. The locked Boto3 release supports Python 3.14; the Hatchling release is not yanked and has PyPI attestation; Ty publishes an immutable signed release with attestations. No other open dependency PR exists. Dependabot security alerts are disabled, so alert review could not be performed without administrative configuration.
* 2026-09-21: Added a three-attempt, two-second, immutable-image acquisition helper for final-image CI and locally built rootless E2E. It retries only classified transport failures, never prints captured upstream URLs, rejects a permanent or integrity error before retrying, and rechecks the exact digest after a successful pull. Focused policy tests cover transient success with a presigned-URL-shaped EOF fixture, retry exhaustion, and permanent/integrity no-retry behavior. Formatting, lint, type, lock, shell-syntax, and focused policy checks pass. The exact PR #248 distributed E2E rerun remains pending coordinated CI capacity.
* 2026-09-21: User approved a bounded scope extension after three independent infrastructure failures: PR #248 distributed E2E reached a GitHub-release Pandoc download that returned HTTP 504 during the Containerfile build; PR #254 reached a pinned UV download that returned HTTP 504; and PR #254 public alignment timed out fetching the GitHub Release receipt. The extension covers only classified transient retries at these checksum-pinned download and read-only public-release boundaries; checksum, signature, digest, redirect, authorization, and not-found failures remain fail-closed and non-retried.
* 2026-09-21: Added bounded Curl retries to all checksum-pinned Containerfile downloads without `--retry-all-errors`; each transfer keeps HTTPS restrictions, a 20-second connect timeout, a 180-second transfer timeout, three retries after the initial transfer attempt, and a 120-second retry budget before its existing checksum/signature validation. Public alignment now retries only 429/5xx responses, timeouts, and connection resets, preserving response-byte limits, trusted redirects, and terminal authentication/not-found/TLS-certificate behavior. Focused policy checks cover retry exhaustion and now pass (118 selected tests), together with formatting, lint, type, lock, and shell-syntax checks. Exact CI reruns and post-merge main validation remain pending.

* 2026-09-21: User explicitly approved the reviewed UBI RPM inventory baseline update after two independent T48 final-image builds failed closed with the same mismatch. The immutable base-image digest and every checksum-pinned non-RPM source are unchanged. Reconstructing the inventory from the approved `5062777d84d38c9d70c8a52c11b84c5e082fc652ec70e2d3255721a00ce031ef` final-image inventory by substituting exactly six rows reproduces the failed `d35b361f72fcb13a8dd683649ba825b6c0363900105c99ded006543e07917292` digest: `curl-minimal`, `libcurl-minimal`, and `libcurl-devel` advance from `0:7.76.1-40.el9_8.5` to `0:7.76.1-40.el9_8.7`; `openssl`, `openssl-libs`, and `openssl-devel` advance from `1:3.5.5-6.el9_8` to `1:3.5.8-1.el9_8`. Package names, licenses, architectures, `tar` (`2:1.34-13.el9_8`), and all three imported RPM GPG public-key rows are unchanged. Updated only the fail-closed inventory digest and review comment; no source pin, retry, signature, workflow, release, or public image pin changes. Hosted build, security scan, both final-image E2E storage profiles, CI gate, and post-merge main validation remain pending.

* 2026-09-22: User explicitly authorized correcting the reverse CLI deadline race and resuming publication, checks, squash merge and cleanup for PRs #248/#253. The final status poll can exhaust the global wait budget inside the HTTP client and currently report network_error instead of wait_timeout. Translate only network_error observed after the global monotonic deadline; preserve earlier network failures and every non-network failure. Implemented the focused prerequisite on `fix/T86-cli-wait-deadline`; independent review approved the precise error boundary. All 46 targeted CLI tests passed, including a real loopback HTTP timeout. Global Ruff formatting/lint and ty passed. The canonical engine-excluded suite passed 4,418 tests (56 deselected, 11 warnings), with 94.96% total and 91.20% branch coverage; source HEAD and binary diff were unchanged throughout. Exact-head final-image checks, the engine-marked cases, and main verification remain pending.


* 2026-09-22: PR #257 merged at `582886e5c87799b6de19a5fc3c0369916055f7a0` after every exact-head check passed, including both final-image E2E profiles. Its source branch was removed after verification. Post-merge run `35669415349` passed all tests, but the coverage job failed before aggregation because GitHub's artifact intermediary returned HTTP 403. Both same-attempt reports exist and their independently downloaded combination passes the unchanged total and branch thresholds (90.08% branches, 6,329/7,026). This diagnostic does not replace a successful main gate. A full same-source rerun, with no code or permission change, awaits renewed user authorization; the complete attempt keeps both shard artifacts consistent. PR #248 is prepared locally at `dda91c267f875e54a04ff63b7697d6d26257534a` with the identical reviewed dependency patch; 15 real PostgreSQL/S3, clean-install and HTTP CLI tests and all static/lock checks pass. Its publication and PR #253's resumed cycle await main verification.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
implementation boundaries, or progress changes.
