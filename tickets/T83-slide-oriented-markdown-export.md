---
ticket: T83
linear_id: G1L-582
linear_url: https://linear.app/g1lom/issue/G1L-582/t83-preserve-slide-structure-in-powerpoint-to-markdown-exports
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T83 - Preserve slide structure in PowerPoint to Markdown exports

## Objective

Complete the remaining reverse-conversion scope from T82: provide opt-in slide-oriented Markdown/Marp output for edited PowerPoint presentations in 2md. Ordinary anydoc extraction remains available in 0.7.0; original source recovery in a 2pptx bundle is not edited-slide extraction.

## Acceptance criteria

* Qualify pinned anydoc slide boundary, presenter note and image capabilities before implementation.
* Offer explicit slide separators, optional presenter notes/images and Markdown or Marp output without silently dropping slide content.
* If required, implement only a narrowly bounded non-executing OOXML presentation reader inside the existing isolated reverse attempt, retaining archive/XML/image limits, no network, authorization, cancellation, recovery and retention.
* Persist options and include them in idempotency for both SQLite and PostgreSQL profiles; expose matching HTTP/CLI/browser contracts and regenerated bindings.
* Add unit/security, actual-boundary integration, both-storage and final rootless image E2E coverage, including critical failure/authorization behavior.
* Document source recovery versus extraction, fidelity limitations and safe image packaging; no OCR, arbitrary CSS, executable formats or full visual round-trip promise.

## Dependencies

T82 forward PowerPoint baseline; T69-T72 reverse-conversion isolation and lifecycle.

## Progress

2026-09-20: Split from T82 during user-authorized 0.7.0 publication preparation so unfinished reverse options remain explicitly tracked. No reverse parser or option implementation is claimed.

