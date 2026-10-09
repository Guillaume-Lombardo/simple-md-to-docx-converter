---
ticket: T109
linear_id: G1L-633
linear_url: https://linear.app/g1lom/issue/G1L-633/t109-relocate-completed-spikes-and-archive-historical-evidence
status: Backlog
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T109 - Relocate completed spikes and archive historical evidence

## Objective

Separate production assets from completed qualification spikes and historical evidence.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to remove or freeze).

## Acceptance criteria

- Move the `spikes/anydoc` files used in production (reverse-attempt Containerfile, contract, supply-chain and corpus files referenced by tests and scripts) to a production location such as `toolchain/anydoc`; update every reference.
- Move `spikes/composer` fixtures still used by web tests next to those tests; archive the remaining probes.
- Move `docs/evidence/`, `docs/t00-toolchain-validation.md` and `docs/nextjs-migration-architecture.md` under `docs/archive/` with a short index, or delete what the owner no longer needs.
- Image builds, the reverse-attempt smoke, unit, integration, web and documentation tests pass with unchanged image bytes where possible.

## Dependencies

- None.

## Blocking points

- `spikes/anydoc` is referenced by `containers/reverse-attempt/Containerfile`, `scripts/ci/select_domains.py`, `scripts/e2e/run.sh`, smoke scripts and several tests; it cannot simply be deleted.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-633.
