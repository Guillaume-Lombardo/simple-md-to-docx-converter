---
ticket: T129
linear_id: G1L-682
linear_url: https://linear.app/g1lom/issue/G1L-682/t129-align-recorded-engine-versions-with-the-shipped-toolchain
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T129 - Align recorded engine versions with the shipped toolchain

## Objective

Make the conversion component versions recorded by admission, workers and job traceability match the engines that the final image actually ships, and prevent future drift. Found on 2026-10-11 during T106: `COMPONENT_VERSIONS` in `src/markweave/conversion/runtime_manifest.py` records Mermaid CLI `11.16.0`, while `toolchain/document-engines/package.json`, its lock, `scripts/container/smoke.sh` and the final image ship `11.17.0` (bumped in T80, PR #236, without updating the recorded version). Job traceability manifests therefore report a version that was not used.

## Acceptance criteria

- `COMPONENT_VERSIONS` records the versions shipped by the final image for every listed component (Chromium, LibreOffice, Mermaid CLI, Pandoc).
- A unit test fails when a recorded version differs from its single source of truth in the repository (toolchain package manifest/lock, container build pins, or smoke expectations), so a future engine bump cannot leave the recorded version stale.
- The final-image smoke check or an existing final-image test verifies that the recorded versions equal the executables' reported versions.
- Any documentation listing these versions is consistent.

## Dependencies

- None.

## Blocking points

- None known. Changing which engine version is approved is out of scope; this ticket only records the versions already approved and shipped.

## Progress

- 2026-10-11: Created in Linear and mirrored here from the T106 finding. Started on branch
  `fix/T129-engine-version-pins` from `origin/main` `7e4f81a`. Audit of the four engines:
  Chromium `151.0.7922.173`, LibreOffice `26.2.5.2` and Pandoc `3.10.2` already matched the
  Containerfiles, the document-engine CI setup and the final-image smoke expectations; only
  Mermaid CLI was stale (`11.16.0` recorded, `11.17.0` shipped). `COMPONENT_VERSIONS` now records
  Mermaid CLI `11.17.0`, and the production LibreOffice PDF configuration reads its version from
  `COMPONENT_VERSIONS` instead of a duplicate literal. The new unit test
  `tests/unit/conversion/test_runtime_manifest.py` compares every recorded engine version with
  the Mermaid package manifest and lock, both Containerfiles, the LibreOffice archive script, the
  CI document-engine expectations and the smoke expectations, and requires every recorded
  component to have such a source. `scripts/container/smoke.sh` now compares the installed
  `COMPONENT_VERSIONS` with the versions reported by `pandoc`, `mmdc`, `google-chrome-stable`,
  `soffice` and the installed application inside the hardened final image. Run against the
  local `main` final image, that check rejects the stale `11.16.0` value and accepts the
  corrected manifest. Test fixtures and the Mermaid and T00 documentation now state `11.17.0`
  for the shipped toolchain while the T00 evidence keeps its original probe record.
- 2026-10-11: Completed and verified on `main`. PR #289 was squash-merged as
  `ed03bdfa8ef5300d0692f44c3e4e3d8823f4e367` from head `8772cf6`, a `main`-update merge of
  reviewed head `9489ec7`. Independent review round 1 approved it with no findings; hosted CI run
  38134149393 passed at the reviewed head and PR CI run 38153593707 passed at the merged head.
  Main CI run 38156378579 at `ed03bdf` passed all 16 selected jobs, including
  `document-engines`, `container`, both final-image E2E profiles and `CI / gate`. Linear G1L-682
  is Done. Per-criterion evidence:
  - `COMPONENT_VERSIONS` records the shipped Chromium `151.0.7922.173`, LibreOffice `26.2.5.2`,
    Mermaid CLI `11.17.0` and Pandoc `3.10.2`.
  - `tests/unit/conversion/test_runtime_manifest.py` fails when a recorded version differs from its
    repository source of truth; the review's negative checks confirmed this for every engine.
  - `scripts/container/smoke.sh` compares the installed recorded versions with the executables'
    reported versions in the hardened final image; the `container` job passed it on `main`.
  - The Mermaid and T00 documentation state `11.17.0` for the shipped toolchain; the T00 evidence
    keeps its original probe record.
