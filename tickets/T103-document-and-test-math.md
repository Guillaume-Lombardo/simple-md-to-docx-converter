---
ticket: T103
linear_id: G1L-622
linear_url: https://linear.app/g1lom/issue/G1L-622/t103-document-and-test-math-rendering
status: Done
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T103 - Document and test math rendering

## Objective

The approved reader already turns `$...$` and `$$...$$` into native Word equations (OMML); make that supported behavior explicit, tested and documented.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to change, item 4).

## Acceptance criteria

- Add inline and display math cases to the T04 corpus and golden tests for DOCX (inspect `m:oMath` in `word/document.xml`), PDF (rasterized golden) and PPTX.
- Cover malformed TeX and confirm the stable failure or literal fallback behavior.
- Clarify the section 5 sentence 'TeX-like text is ordinary text' so it refers to raw TeX only, and document math in `docs/pandoc-docx.md` and `docs/powerpoint.md`.
- Verify with the exact approved Pandoc 3.10.2 artifact (the analysis checked only Pandoc 3.1.3).
- Add a final-image test that fails when the LibreOffice Math module is missing, because without it equations silently disappear from the PDF while the DOCX keeps them.

## Dependencies

- None.

## Blocking points

- Spike 2026-10-09 (Pandoc 3.1.3, LibreOffice 24.2): OMML equations render in the PDF only when LibreOffice Math is installed; confirm with the pinned Pandoc 3.10.2 and LibreOffice 26.2.5 final image.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.
- 2026-10-11: Implementation on branch `feat/T103-math-rendering`.
  - Delivered: `math-equations` and `math-malformed-tex` T04 corpus cases (new `math` category);
    a canonical OMML golden (`tests/corpus/math/golden/omml.json`) shared by DOCX and PPTX; a
    locked PDF raster golden (`tests/corpus/math/golden/pdf`); a final-image smoke check that
    fails when equations are missing from the PDF; the clarified section 5 sentence; math
    documentation in `docs/pandoc-docx.md`, `docs/powerpoint.md`, and `docs/golden-testing.md`.
  - Observed with the exact Pandoc 3.10.2 artifact: `$...$` becomes `m:oMath`, `$$...$$` becomes
    `m:oMathPara`; in PPTX both are wrapped in `a14:m` inside an `mc:AlternateContent` choice
    that requires `a14` and has no fallback. Malformed TeX (`$x^$`, `$\undefinedmacro{x}$`,
    `$$\begin{matrix} a$$`) is a literal fallback: Pandoc exits 0, writes a warning, and keeps the
    source with its delimiters as text. `$\left( x$` is still accepted as an equation.
  - Blocking point confirmed with Pandoc 3.10.2 and LibreOffice 26.2.5.2 in a local final image
    built from the pinned Containerfile: with LibreOffice Math the PDF contains the equations;
    after removing `libreoffice26.2-math` and `libobasis26.2-math` they silently disappear while
    the DOCX keeps them. The new smoke check, the math raster golden, and the PDF math-text test
    pass with the module and fail without it.
  - Tests: `tests/integration/document_engines/test_math_rendering.py` (DOCX/PPTX,
    `requires_pandoc`), new cases in `test_libreoffice_pdf.py` (`requires_pandoc`,
    `requires_libreoffice`), `tests/unit/test_golden_omml.py`, and a static smoke contract in
    `tests/container/test_container_assets.py`.
  - Limitations: the PDF goldens were generated and the LibreOffice tests were run locally
    inside the existing local 0.7.1 final image (pinned Pandoc, LibreOffice and fonts, with the
    current `fonts.conf`), because LibreOffice is not installed on the development host; that
    environment reproduces the committed T11 golden byte for byte. Hosted CI (document-engines
    and container domains) remains the authoritative confirmation. Pandoc's malformed-math
    warning is not surfaced to users because the adapter discards engine output.
- 2026-10-11: Completed and verified on `main`. PR #286 was squash-merged as
  `7e4f81ab84aa665857bb58326092fcf572f2e43c` from head `ba2dcc1` after independent review
  round 1 approved it with no findings. Main CI run 38132219791 passed on attempt 2, including
  `document-engines`, `container`, and both final-image E2E profiles, which confirms the
  locally generated PDF goldens and the LibreOffice Math smoke check on hosted runners.
  Attempt 1 failed only in `e2e-distributed` on the intermittent scanner-outage timeouts
  tracked by T130 / G1L-683. Linear G1L-622 is Done. Documented limitations: Pandoc
  malformed-math warnings are discarded, so the literal fallback is silent to users, and PPTX
  math uses `a14:m` without an `mc:Fallback`.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-622.
