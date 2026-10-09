---
ticket: T127
linear_id: G1L-660
linear_url: https://linear.app/g1lom/issue/G1L-660/t127-publish-linuxarm64-images
status: Backlog
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T127 - Publish Linux/ARM64 images

## Objective

Support ARM64 hosts (for example Apple Silicon development machines and ARM servers).

Created from the repository analysis accepted by the owner on 2026-10-09 (section: competitor features).

## Acceptance criteria

- Multi-architecture backend, frontend and reverse-attempt images with per-architecture pinned and verified engine artifacts.
- Both final-image E2E profiles pass on ARM64 runners.
- Release receipts record every architecture digest.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Requires decision D14.
- Several pinned artifacts are x86-64 only today (anydoc ABI3 wheel; the Chrome build used by Mermaid); ARM64 replacements need supply-chain review.
- ARM64 CI runners are required.
- Sequencing: start only after the in-progress Composer iterations T89, T90 and T91 are Done (work-in-progress limit accepted on 2026-10-09). This is an ordering rule, not a Linear blocking relation.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-660.
