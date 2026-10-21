---
ticket: T147
linear_id: G1L-705
linear_url: https://linear.app/g1lom/issue/G1L-705/t147-download-and-load-final-image-archives-in-parallel
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T147 - Download and load final-image archives in parallel

## Objective

Shorten the fixed setup of every final-image CI job. This is part of the CI speed follow-up to K5
from the T139 audit, approved by the owner on 2026-10-21.

Measured on scheduled main run 39109797384 (2026-10-18). Each E2E shard spends 92 to 97 s in
"Load verified final images" before any test runs. The loads run one after another: backend
57.2 s, frontend 19.5 s, reverse-attempt 14.1 s. e2e-distributed-1 also spent 56 s and 25 s in two
sequential archive downloads.

## Scope

- `scripts/container/ci_images.py load` loads the roles of a domain concurrently. It keeps every
  check per role: archive configuration digest, the ID reported by `podman load`, the tagged ID,
  and refusal to replace an existing image. Any failure stops the load.
- The `final-image` job downloads its domain's archives in a single step, or concurrently, without
  widening what each domain receives.
- The CI validator and its tests are updated, along with the canonical digest of `ci.yml`.
- The release qualification jobs from T134 may reuse the same loader.

## Acceptance criteria

- The unit tests cover concurrent success, a failure in each role while the others succeed, and
  refusal to replace an existing image. Each test fails without its mechanism.
- "Load verified final images" drops from about 95 s to the duration of the longest single load
  plus a small overhead (target under 65 s). Measured on three consecutive full hosted runs, with
  before and after recorded.
- The canonical checks pass.

## Dependencies

- T135 (single image build and ID-verified loading). Done.

## Progress

- 2026-10-21: Created in Linear (G1L-705) from the owner-approved K5 recommendations and started on
  branch `perf/T147-parallel-image-loads` from `origin/main` `95bc188`.
- 2026-10-21: Implemented the loader.
  - `ci_images.load` first checks every role of the domain: published ID, downloaded regular-file
    archive, archive configuration digest, and an unused image name. No archive is loaded until all
    roles pass, which is stricter than before (a later role's defect used to surface only after
    earlier roles had loaded).
  - The `podman load` commands then run concurrently in a thread pool, one per role. The pool waits
    for every load and reports the first failure in role order before any image is tagged. Each
    role then keeps its reported-ID, inspected-ID, tag and tagged-ID checks.
  - Each role still prints `load_seconds`, and the domain prints `concurrent_load_seconds`.
  - The release qualification jobs of T134 call the same loader and benefit without change.
- 2026-10-21: Downloads left unchanged, pending an owner decision. Per-role download durations in
  run 39109797384 were 12 + 5 + 4 s for most E2E shards and 39 s for the backend in `container`;
  only e2e-distributed-1 was slow (56 + 25 + 14 s). The pinned `actions/download-artifact` downloads
  up to five artifacts concurrently when given a `pattern`, so a single step per domain is possible.
  It would replace three validator-bound steps for a typical gain of about 8 s. I propose to drop
  the download bullet from this ticket's scope unless hosted runs show the slow case recurring.
- 2026-10-21: Local verification. `ruff`, `ty`, the CI validator and the container, selection and
  validation tests (596) pass. The new tests (a barrier that only clears when every load runs at
  once; a load failure in each role tagging nothing; a missing archive or an occupied name in each
  role preventing every load) fail 6 of 7 against the previous sequential loader. The seventh, the
  `backend` precondition case, held before too, since backend was checked first. Not run locally:
  `tests/integration/container/test_ci_images_podman.py` (`requires_podman`; this container has no
  Podman) and the hosted durations.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-705.
