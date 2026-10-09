---
ticket: T103
linear_id: G1L-622
linear_url: https://linear.app/g1lom/issue/G1L-622/t103-document-and-test-math-rendering
status: Backlog
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

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-622.
