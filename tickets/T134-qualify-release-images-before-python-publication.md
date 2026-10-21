---
ticket: T134
linear_id: G1L-687
linear_url: https://linear.app/g1lom/issue/G1L-687/t134-qualify-release-images-before-irreversible-python-publication
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T134 - Qualify release images before irreversible Python publication

## Objective

Prevent Python-only partial releases. Today `.github/workflows/release.yml` publishes to PyPI and creates the tag and GitHub Release independently of container construction and qualification. The 0.6.0, 0.7.2 and 0.7.4 releases each ended with PyPI published but no GHCR images, because the container job failed before staging and left nothing to recover. Each case required a one-time public-alignment exception and a corrective version.

Found on 2026-10-12 during T133. The release run 38182704382 for 0.7.4 published PyPI, and the frontend image then failed the Critical vulnerability gate. The independent review of PR #295 recorded that a later RPM repository change, a new scanner finding or a hosted failure can repeat this, even after PR CI passes.

## Acceptance criteria

- **Qualify first.** The release workflow builds, qualifies (smoke, two-profile E2E, release supply-chain gate) and retains the exact staged three-image set before any irreversible external mutation: tag, GitHub Release or PyPI upload.
- **No partial state.** A container build, scan or E2E failure leaves no PyPI version, tag or Release behind. The retry path stays the existing attempt mechanism, without a new version.
- **Existing guarantees unchanged:**
  - the exact reviewed SHA;
  - a single build per image;
  - publishing the staged bytes without rebuilding;
  - OIDC trusted publishing;
  - attestations;
  - evidence binding.
- **Tests and docs.** Workflow validation tests and `docs/releasing.md` are updated. Release workflow tests cover a container failure before Python publication.

## Extended scope (owner decision 2026-10-12, CI speed and reliability lot 3)

Also split the 48.6-minute release `container / build-and-publish` job (run 38223418529). It builds and smoke-tests the images (10.6 min), generates SBOMs (4.8 min), runs both E2E profiles serially (28 min) and publishes.

The new layout:

1. A `stage` job builds and serializes each image once, verifies the result, and uploads the staging artifact.
2. Parallel jobs then run the smokes, SBOM and vulnerability scanning, and the E2E shards from T135. Each loads the staged bytes and verifies the image ID or digest against the staged metadata.
3. `publish` needs all of them and copies the exact staged bytes.

Every release job must finish well under 20 minutes, and only the publishing job holds `packages: write`. Combined with the objective above, Python publication, tag and Release creation then happen only after the complete image set is qualified and staged.

## Dependencies

- T133 (the 0.7.5 corrective release), so that the workflow does not change in the middle of a release. Done.
- T135 (E2E phase selector and shard layout), because the release reuses it. Done.

## Blocking points

- None known. Any change to the PyPI trusted-publisher identity (workflow file or environment name) must be coordinated as described in `docs/releasing.md`.

## Progress

- 2026-10-12: Created from the T133 partial 0.7.4 release and extended with lot 3 of the CI speed plan.
- 2026-10-21: Started on branch `chore/T134-qualify-release-before-publication` from `origin/main`
  `95bc188`.
- 2026-10-21: Implemented.
  - **New read-only reusable workflow `container-qualify.yml`.** `stage` builds the backend,
    frontend and reverse-attempt images once through `scripts.container.ci_images build`, with the
    reviewed commit timestamp exported as `SOURCE_DATE_EPOCH` (the same release build semantics),
    and transfers each OCI archive within the run. `smoke`, `supply-chain` and a four-job `e2e`
    matrix (both shards of both profiles) load the archives only after `ci_images load` proves the
    archive, loaded and tagged image IDs equal the stage output. `smoke` keeps the release smoke
    list unchanged; `supply-chain` keeps the three `supply-chain.sh ... release` gates and retains
    `container-stage-v<version>` ("Retain staged pair before registry mutation", 90 days). Every
    job is `contents: read` with a 30-minute bound; none holds a registry or OIDC credential.
  - **`release.yml`.** `container-qualify` runs in parallel with the Python build.
    `create-release` needs `build-and-verify` and `container-qualify`; `publish` (PyPI) and
    `container` also need `container-qualify`. A qualification failure therefore leaves no tag,
    Release, PyPI version or GHCR tag. The `pypi` environment and `release.yml` publisher identity
    are unchanged.
  - **`container-release.yml`.** `build-and-publish` becomes `publish`: it verifies the Release
    identity, downloads the qualified staged set from the same run and publishes it with the
    unchanged `publish-release-pair.sh`; it no longer builds, smokes, scans or runs E2E. Recovery
    now requires the `container-qualify / supply-chain` staging step and every
    `container-qualify` job of the source run to have succeeded. `attest` and `release-evidence`
    keep their behaviour; as before, `attest` and `recover-evidence` also hold `packages: write`.
  - **Validator.** `scripts/ci/validate_ci.py` gains `validate_container_qualify_workflow_text`
    (canonical digest, exact jobs and permissions, trusted-main guards, bounded timeouts, single
    stage consumed by every job, verified-ID loads, all E2E domains, smoke and evidence commands,
    staged retention, no credentials or caches) and `_validate_staged_publication` (publication
    downloads the staged set before push and never builds or requalifies). The production policy
    requires `create-release`, `publish` and `container` to need `container-qualify` without a
    status function that would run them after a failed dependency.
  - **Docs.** `docs/releasing.md` (steps 5 to 7 and recovery), section 11 of the product
    specification, `docs/local-development.md` and the `run.sh` shard comment.
- 2026-10-21: Local verification. `uv run python -m scripts.ci.validate_ci` passes; actionlint
  1.7.12 reports nothing on the four workflows; `tests/test_ci_validation.py`, the container and
  release tests (749) pass; the `unit or light_coverage` selection has only the 7 sandbox
  failures that also fail on unmodified `main` in this container. The new validator code is fully
  covered by mutation tests. Not verifiable locally: a hosted release run (it requires a version
  transition on `main`); the first release after merge is the end-to-end evidence, and its job
  durations must be recorded against the 20-minute target.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-687.
