---
ticket: T151
linear_id: G1L-710
linear_url: https://linear.app/g1lom/issue/G1L-710/t151-stop-retaining-the-uv-build-cache-in-the-python-images
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T151 - Stop retaining the uv build cache in the Python images

## Problem

The owner's scan of the backend CI archive found a retained uv cache under `/opt/app-root/src/.cache/uv`, about 114 MiB of regular files, which held another copy of the affected Pillow wheel. Both `Containerfile` and `containers/reverse-attempt/Containerfile` ran `uv sync` and then removed `/root/.cache/uv`, but the build never uses that directory, so the real cache stayed in the image. T150 (G1L-708, #325) upgrades Pillow but does not change the cache handling.

## Scope

- Install both Python images with `uv sync --no-cache`, so no uv cache is written to an image layer.
- Drop the ineffective `rm -rf /root/.cache/uv`.
- Add a regression test that both `uv sync` lines carry `--no-cache` and that neither Containerfile references a uv cache path.

## Acceptance criteria

- Neither the backend nor the reverse-attempt final image contains a uv cache directory.
- `CI / build images`, `CI / container`, `CI / supply-chain` and both final-image E2E profiles pass.
- The canonical checks pass.

## Dependencies

- T150 (G1L-708, #325): this follows it and is rebased after it merges.

## Progress

- 2026-10-21: Created as the follow-up the owner chose for the uv cache gap left by T150. Locally, the container asset tests pass. The image contents remain to be verified in hosted CI.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-710.
