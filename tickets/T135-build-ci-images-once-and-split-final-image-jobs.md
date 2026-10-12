---
ticket: T135
linear_id: G1L-688
linear_url: https://linear.app/g1lom/issue/G1L-688/t135-build-ci-images-once-and-split-long-final-image-jobs
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T135 - Build CI images once and split long final-image jobs

## Objective

Bring every heavy CI job well under GitHub's 20-minute concern by building each image once per run
and splitting the long serial final-image jobs. This is lot 2 of the CI speed and reliability plan
the owner approved on 2026-10-12.

Measured baseline (runs 38235590401 and 38150642257):

- **Duplicate builds.** The backend and reverse-attempt images are built 3 times per run and the
  frontend 4 times, each build re-fetching every upstream input.
- **E2E.** e2e-standalone and e2e-distributed take 18.5–20.5 min each: about 4.5 min of image
  build plus 13.7–15.4 min of serial tests.
- **Container.** The container job takes 15–17.6 min, including 3.3 min of supply-chain scanning.
- **Compose.** The compose job takes 12–15 min across 4 serial sub-suites.

## Acceptance criteria

- **Single build.** A `build-images` CI job builds the backend, frontend and reverse-attempt images
  once per run, after pre-pulling base images with retry. It publishes them as run-scoped
  OCI-archive artifacts and exposes their image IDs.
  - Consumer jobs (container smokes, final-image E2E, frontend, supply chain) download only what
    they need, load it, and fail closed unless the loaded image ID equals the build output.
  - No cache writes from untrusted contexts and no registry publication outside releases.
- **E2E shards.** Each final-image E2E profile is split into at least two shards through an
  explicit phase selector in `scripts/e2e/run.sh`.
  - Every scenario runs in exactly one shard, enforced by a validator test.
  - Prerequisite state is re-seeded per shard instead of shared implicitly.
  - Every scenario still runs against the final rootless images in both storage profiles.
- **Supply chain.** Supply-chain SBOM and vulnerability scanning runs as its own job against the
  shared archives, with the same gate semantics.
- **Compose.** The compose sub-suites run as separate jobs.
- **Gate.** `CI / gate`, domain selection and the fail-closed semantics for skipped or missing
  domains are preserved. `scripts/ci/validate_ci.py` and its tests are updated.
- **Durations.** Every heavy job completes in about 12 minutes or less in at least three
  consecutive full-suite runs. Record the before and after durations.

## Scope notes (owner plan, 2026-10-12)

- The release workflow restructure is T134 and stays out of scope. `container-release.yml` and
  `release.yml` are unchanged; the release still runs the complete serial E2E suite.
- The ClamAV stop waits and `compose.yaml` are unchanged.
- Workflows are dispatched by the orchestrator after review, not by the implementation.

## Dependencies

- T130 (lot 1: flake fixes, network hardening, `max-parallel`) merged first, because both change
  the CI workflow and the E2E harness. Merged in #297 (`a10645a`).

## Blocking points

- Shard boundaries must respect hidden state coupling in `scripts/e2e/run.sh`.
- Artifact transfer of about 2.2 GB of images must be measured; if it is too slow, reconsider the
  archive format or compression.

## Progress

- 2026-10-12: Mirrored from Linear and started on branch `chore/T135-build-images-once` from
  `origin/main` `9306cab`.
