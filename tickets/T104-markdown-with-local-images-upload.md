---
ticket: T104
linear_id: G1L-623
linear_url: https://linear.app/g1lom/issue/G1L-623/t104-accept-markdown-with-local-images-without-a-hand-built-zip
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T104 - Accept Markdown with local images without a hand-built ZIP

## Objective

Let users convert a Markdown file that references local images by selecting or dropping the Markdown file together with its images (or a folder), instead of building a ZIP by hand.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to change, item 3).

## Acceptance criteria

- Browser 2docx and 2pptx workspaces accept a multi-file or folder selection and drag and drop; the CLI accepts a Markdown path and collects the referenced local images.
- The browser or CLI client packages the files client-side into the existing approved archive boundary and the HTTP input contract is unchanged (D2); all archive, path, image, SVG, size and count limits and ClamAV scanning still apply.
- Missing or escaping image references produce the existing stable validation errors.
- Unit, integration and final-image E2E tests in both profiles cover the primary path, missing image, path traversal and oversize cases.
- Update the conversion UI, CLI and capability documentation.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Decision D2 recorded in specification section 3.7: client-side packaging, unchanged HTTP input contract.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.
- 2026-10-10: Implementation started on branch `feat/T104-markdown-local-images`. The 2docx and
  2pptx workspaces accept multi-file, folder, and dropped-folder selections and package the
  Markdown file with its referenced local images in the browser; `markweave convert` packages a
  Markdown path's referenced local images. Both build the approved layout (root `document.md`
  plus referenced images, stored ZIP), keep the HTTP input contract unchanged, and pre-check
  missing, escaping, and invalid references with the service's stable validation messages, which
  a shared fixture ties to the service parser. Unit, integration, and final-image E2E coverage
  (CLI workflow and a new browser test in both profiles) is added; documentation is updated.
  Awaiting hosted CI, final-image E2E evidence, and independent review.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-623.
