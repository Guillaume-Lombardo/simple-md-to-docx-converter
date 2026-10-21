---
ticket: T150
linear_id: G1L-708
linear_url: https://linear.app/g1lom/issue/G1L-708/t150-adopt-pillow-13-for-the-critical-jpeg-decoder-advisory-and-retain
status: Done
priority: Urgent
project: Markdown to DOCX and PDF Converter
---

# T150 - Adopt Pillow 13 for the critical JPEG decoder advisory and retain rejected scans

## Problem

`CI / supply-chain` rejects every pull request with `error: fixable Critical vulnerabilities found`
since the Grype database built on 2026-10-21 06:31 UTC. An independent scan of the retained backend
archive identified Pillow 12.3.0, GHSA-8g5g-8vmr-9782 / CVE-2026-112403 (JPEG decoder buffer
overflow from scanlines wider than tile extents), rated Critical; versions up to 12.3.0 are affected
and 13.0.0 is patched. `pyproject.toml` constrains Pillow to `>=12,<13` in both the `server` and
`reverse-attempt` extras, so dependency PR #313 cannot select the fix.

The failed run also uploaded no scan evidence: `supply-chain.sh` discards its staging directory when
the gate rejects, so the log did not name the component or advisory.

## Scope

- Move the supported Pillow bound to `>=13,<14` in both extras and lock Pillow 13.0.0.
- Qualify image, PDF and Office behaviour that uses Pillow (image normalization, Mermaid
  normalization, PDF goldens, Composer Office image edits) and both images that ship Pillow (backend
  and reverse-attempt).
- Retain the rejected, unverified scan evidence when the gate fails, name every blocking finding in
  the log, and scan both CI images before failing.
- Keep the vulnerability gate unchanged. No version bump or release.

## Acceptance criteria

- `uv.lock` resolves Pillow 13.0.0 and both extras require `>=13,<14`.
- `CI / supply-chain` passes with the unchanged gate on the fix head.
- The canonical local checks pass, and the hosted image, document-engine, container and E2E domains
  pass.
- A rejected scan keeps its SBOM, vulnerability report and summary, carries no release-bundle
  manifest, and still fails the job.

## Dependencies

None. Unblocks the supply-chain gate on every open pull request, including the T92 stack (#316).

## Progress

- 2026-10-21: Created from the owner's diagnosis on PR #316.
- 2026-10-21: Started on branch `fix/T150-pillow-13-security` from `origin/main` `09b37c2`.
  - Pillow 13.0.0 (released 2026-10-16) fixes the Critical JPEG decoder advisories
    (CVE-2026-112398, CVE-2026-112403) and the other advisories listed in its release notes. Its
    backward-incompatible changes (Python 3.10 support, clipped I-mode PNG saves,
    `Image.fromarray` mode changes, `get_child_images`, `Image._show`,
    `ImageCmsProfile.product_name`/`product_info`, `ExifTags.IFD.Makernote`) touch no repository
    code: only `conversion/images.py`, `conversion/mermaid.py` and
    `scripts/generate_t11_pdf_golden.py` import Pillow, and none of them uses those APIs.
  - `supply-chain.sh` now moves a rejected scan's staging directory to the output directory without
    a release-bundle manifest and still exits with the gate's status. `run-ci-supply-chain.sh` scans
    the reverse-attempt image even after a backend rejection and then fails with the first
    rejection. `summarize_supply_chain.py` prints each blocking finding (advisory, package and fixed
    versions).
  - `docs/composer-preview.md` still pins `pillow==12.0.0` in a local, offline fixture-generation
    recipe whose recorded fixture digests depend on it; it does not ship and is left unchanged.
- 2026-10-21: Local evidence with Pillow 13.0.0.
  - `ruff format --check`, `ruff check` and `ty check` pass.
  - The default local command (`-n 4`) ran 6,383 passed and 16 failed with 94.32% coverage. The
    same files rerun on Pillow 12.3.0 (`uv run --with pillow==12.3.0`) reproduce 15 of those
    failures: broker process factory, local test services lifecycle and RustFS (no Docker daemon
    here), the subreaper harness, the engine `/proc` isolation probe and the runtime doctor. The
    16th, a fake-engine Pandoc boundary case, timed out under parallel load and passes serially
    on Pillow 13.
  - The Pandoc-backed image, media and SVG tests pass (8). The pinned Mermaid, LibreOffice 26.2
    and Podman image suites are not runnable here and need hosted CI.
- 2026-10-21: Executable regressions for the rejection workflow, requested in review. The CI wrapper
  runs with a fake per-image scan for the four backend/reverse status pairs and must call both
  scans and return the first nonzero status. The real `supply-chain.sh` runs against fake tool
  downloads, Syft, Grype and image runtime: a rejected summary leaves the SBOMs, vulnerability
  report, summary and image archive in the output directory with no release-bundle manifest, no
  staging or tool directory, and a bundle that does not verify; an accepted summary prints a
  manifest digest that the real `verify_supply_chain` accepts. Removing the retention move or
  restoring the stop-at-first-failure wrapper makes the corresponding tests fail.
- 2026-10-21: Done. Squash-merged as #325 into `main` at
  `ddd91fa394a440234c45ec5050da16001cfd5216`, whose tree matches the reviewed head `636ba2c`.
  All required final-head checks passed in hosted run 39621557601, including the unchanged
  supply-chain gate for the backend and reverse-attempt images, the document engines, the
  containers and all four final-image E2E shards. Every acceptance criterion is met; no
  limitation remains. The offline Pillow 12 fixture-generation recipe in
  `docs/composer-preview.md` stays tied to its recorded fixture digests and does not ship.
