---
ticket: T147
linear_id: G1L-705
linear_url: https://linear.app/g1lom/issue/G1L-705/t147-load-final-image-archives-in-parallel
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T147 - Load final-image archives in parallel

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
- Out of scope (owner decision 2026-10-21): parallel archive downloads. They take about 20 s per
  shard (12 + 5 + 4 s), so the gain of about 8 s does not justify rewriting the validator-bound
  download steps.
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
  It would replace three validator-bound steps for a typical gain of about 8 s.
- 2026-10-21: The owner removed parallel downloads from the scope; the title and scope are updated
  in Linear and here.
- 2026-10-21: Local verification. `ruff`, `ty`, the CI validator and the container, selection and
  validation tests (596) pass. The new tests (a barrier that only clears when every load runs at
  once; a load failure in each role tagging nothing; a missing archive or an occupied name in each
  role preventing every load) fail 6 of 7 against the previous sequential loader. The seventh, the
  `backend` precondition case, held before too, since backend was checked first. Not run locally:
  `tests/integration/container/test_ci_images_podman.py` (`requires_podman`; this container has no
  Podman) and the hosted durations.
- 2026-10-21: Hosted evidence does not meet the target. Same-minute pair on `main` `09b37c2`, all
  E2E jobs green, "Load verified final images" step durations:

  | Shard | Sequential, #322 run 39604675291 | Concurrent, #323 run 39604676996 | `concurrent_load_seconds` |
  |---|---|---|---|
  | e2e-standalone-1 | 93 s | 86 s | 85.4 |
  | e2e-standalone-2 | 95 s | 84 s | 83.1 |
  | e2e-distributed-1 | 72 s | 79 s | 77.3 |
  | e2e-distributed-2 | 80 s | 84 s | 82.6 |
  | mean | 85.0 s | 83.3 s | |

  An earlier cancelled run (39601973706) gave the same picture: 79–89 s concurrent against 86–93 s
  sequential in #322 run 39601966396. Per-role times under concurrency are consistent: backend
  76.9–86.8 s, frontend 46.4–49.7 s, reverse-attempt 24.8–27.1 s, adding up to about 160 s of work
  in about 85 s of wall time. The loads overlap, but each slows down, so aggregate throughput stays
  flat at about 2.3 GB of archive in about 85 s: a shared bottleneck in the Podman store or the
  disk, not per-archive latency. The barrier test proves overlap, not real-store throughput.
- 2026-10-21: Added bounded, non-gating loader diagnostics to find that bottleneck. Before loading,
  the loader prints the Podman version, graph driver and options, and the block device behind the
  archive directory, the graph root and the image-copy temporary directory (`imageCopyTmpDir`,
  default `/var/tmp`). After the concurrent loads it prints, per device, the MiB read and written
  and the busy percentage from `/proc/diskstats`. A diagnostics failure only prints a notice. The
  hypotheses this separates are a slow graph driver (for example `fuse-overlayfs` or `vfs`), the
  double write of each `oci-archive` (extraction into the copy directory, then layer commit) on
  the same device, and a saturated single disk. The measured correction follows from the next
  hosted run; the accepted under-65-second target stands unless the owner decides otherwise.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-705.
