---
ticket: T106
linear_id: G1L-626
linear_url: https://linear.app/g1lom/issue/G1L-626/t106-add-local-serverless-cli-conversion
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T106 - Add local serverless CLI conversion

## Objective

Provide `markweave convert` for local files, without a server, an account or ClamAV, reusing the same validation and engine pipeline.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to change, item 2).

## Acceptance criteria

- A local command (for example `markweave convert doc.md --to docx,pdf --template t.docx -o out/`) runs the existing validation, archive, image, Mermaid, Pandoc and LibreOffice adapters with the same limits.
- It records the engine versions it used, fails clearly when a required engine is missing, and warns when a version differs from the approved pin; `--require-pinned` turns that warning into a failure (D1).
- A documented container invocation runs the command with the bundled engines.
- Business HTTP commands are unchanged.
- Unit, integration (real engines, marked) and final-image E2E tests cover success, missing engine, invalid input, timeout and cancellation.
- Update `docs/cli.md`, `docs/conversion-cli.md` and README.

## Dependencies

- T96 - Approve the 2026-10 scope changes in the product specification

## Blocking points

- Decision D1 recorded in specification section 3.7.
- Local mode depends on host engines; reproducibility guarantees must be stated honestly when host versions differ from the pinned toolchain.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.
- 2026-10-11: Implemented `markweave convert SOURCE --local` on branch
  `feat/T106-local-cli-conversion`. `markweave.conversion.local` validates the source and an
  optional DOCX template with the service validators, then composes the existing archive, image,
  Mermaid, Pandoc, and LibreOffice adapters through `build_document_engines`, now shared with the
  production worker processor, with the same `MARKWEAVE_*` limits loaded by
  `LocalConversionSettings` (no storage, account, job, or ClamAV settings). Required engines
  (Pandoc; LibreOffice for PDF; Mermaid CLI and Chromium only for Mermaid documents) are probed with
  isolated, bounded `--version` runs against the shared `COMPONENT_VERSIONS` pins; a missing engine
  fails with `engine_missing`, a different or unknown version warns and is recorded, and
  `--require-pinned` fails with `engine_version_unpinned`. Outputs and a content-free
  `*.traceability.json` manifest are published atomically and owner-only; the global `--timeout`
  bounds the run, and SIGINT/SIGTERM cancel cooperatively by terminating the engine process group.
  Remote options are refused with `--local` and local options without it, so HTTP business
  commands are unchanged. `examples/local-conversion.env` carries the quickstart values.
  Tests: unit coverage for settings parity, probes, pipeline, and CLI; integration tests with the
  real launcher and Pandoc, LibreOffice, and Mermaid markers, plus fake slow engines for timeout and
  signal cancellation; `tests/e2e/local-conversion-final-image.sh`, wired into the container CI
  runner, covers the documented container invocation, PDF with template and pins, Mermaid, missing
  engine, invalid input, unpinned engine, timeout, and `podman stop` cancellation; it passed locally
  against a final image built from this branch. Docs: `docs/cli.md`, `docs/conversion-cli.md`,
  README. T129 (#289) aligned the recorded Mermaid CLI pin with the 11.17.0 shipped in the image, so
  after merging `main` the final image accepts Mermaid documents with `--require-pinned` and the
  earlier mismatch note was removed from `docs/conversion-cli.md`. Limitations: host fonts are not
  verified; PPTX output and client-side packaging of Markdown with local
  images are out of scope for local mode.
- 2026-10-11: Completed and verified on `main`. PR #287 was squash-merged as
  `4280275d676a1bfbc522ce9e5282a6c6c27c52ba` from head `63b0e87` after six independent review
  rounds; round 6 approved that exact head with no remaining findings. PR CI run 38161431692
  passed at that head, and push CI run 38164507559 on `main` at `4280275` passed all 19 jobs,
  including `container`, `document-engines`, `e2e-standalone`, `e2e-distributed` and `CI / gate`.
  Linear G1L-626 is Done. Per-criterion evidence:
  - `markweave convert SOURCE --local` runs the service validators and the shared
    `build_document_engines` adapters with the same `MARKWEAVE_*` limits.
  - Engine versions are probed and recorded in the traceability manifest; a missing engine fails
    with `engine_missing`, a different version warns, and `--require-pinned` fails with
    `engine_version_unpinned`.
  - The documented container invocation is exercised by
    `tests/e2e/local-conversion-final-image.sh` in the `container` job.
  - Business HTTP commands are unchanged; remote and local options are mutually refused.
  - Unit, real-engine integration and final-image E2E tests cover success, missing engine,
    invalid input, timeout and cancellation.
  - `docs/cli.md`, `docs/conversion-cli.md` and README document the command.
  Limitations unchanged: host fonts are not verified; PPTX output and client-side packaging of
  Markdown with local images are out of scope for local mode.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-626.
