---
ticket: T134
linear_id: G1L-687
linear_url: https://linear.app/g1lom/issue/G1L-687/t134-qualify-release-images-before-irreversible-python-publication
status: Backlog
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

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-687.
