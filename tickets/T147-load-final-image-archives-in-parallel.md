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
- 2026-10-21: Diagnostics result, run 39633287852 (green). All four E2E shards report Podman 4.9.3
  with the native `overlay` driver and no mount program, so the graph driver is not the cause. The
  archives, the graph root and `/var/tmp` sit on one device (8:1 or 259:1). For about 2.3 GB of
  archives, that device wrote 10.7 to 14.0 GB (about 165 MB/s) and was 34 percent busy, with
  `concurrent_load_seconds` of 82.8, 77.1, 84.6 and 66.4. These runs do not show whether that
  rate is a fixed runner limit. Each
  `podman load` of an `oci-archive` first extracts the archive into the image-copy directory and
  stages the layer blobs there, then commits the layers into the store, so the copy directory alone
  receives about twice the archive bytes.
- 2026-10-21: Correction. When `/dev/shm` has room for twice the archive bytes plus 512 MiB, the
  loader creates a private `mkdtemp` directory there and passes it to every `podman load` as
  `TMPDIR`, which Podman uses as its image-copy directory. That removes about 4.6 GB of disk writes
  from each shard. Otherwise it prints `load_copy_dir=default` and keeps Podman's default. The
  directory is removed after the loads, including on failure. Every per-role check is unchanged,
  downloads stay sequential, and no workflow or runner setting changes. New unit tests cover the
  memory directory passed to every load and removed afterwards, the fallback when space is short
  or cannot be read, and removal after a failed load; the first two fail without their mechanism.
- 2026-10-21: Hosted result of the memory copy directory, run 39638120034 (`fdb5299`, all E2E
  jobs green), against the sequential loader of #322 run 39637450555 on the same `main`
  `b13fae9`, a few minutes apart. Both runs already use the smaller images of T151.

  | Shard | Sequential step | Concurrent step | Backend load, sequential → concurrent |
  |---|---|---|---|
  | e2e-standalone-1 | 83 s | 57 s | 51.2 → 55.3 s |
  | e2e-standalone-2 | 93 s | 67 s | 55.7 → 64.4 s |
  | e2e-distributed-1 | 94 s | 78 s | 58.1 → 75.5 s |
  | e2e-distributed-2 | 72 s | 69 s | 44.9 s → not captured |
  | mean | 85.5 s | 67.8 s | |

  Every job used `/dev/shm` (about 7.99 GiB free, 4.83 GiB required). Disk writes fell from 10.7–14.0
  GB to 6.9–8.5 GB, and the disk was 20 to 66 percent busy. The step now ends with the backend
  load, which takes 4 to 17 s longer than alone while the frontend and reverse-attempt loads run
  beside it (frontend 16–21 s alone, 35–40 s concurrently). The mean misses the under-65-second
  target, and three shards are above it.
- 2026-10-21: Second attempt, unsuccessful: CPU priority (`a804e24`). The largest archive kept
  normal priority and the other loads ran under `nice -n 10`. The loader also printed the CPU busy
  and iowait shares from `/proc/stat`. Run 39642307514 (all jobs green) against the sequential
  baseline of `main` push run 39640756703 on `a143a09`:

  | Shard | Sequential step | Priority step | Backend load | CPU busy / iowait | Disk busy, written |
  |---|---|---|---|---|---|
  | e2e-standalone-1 | 78 s | 69 s | 65.8 s | 43 / 17 % | 71 %, 8013 MiB |
  | e2e-standalone-2 | 90 s | 66 s | 63.8 s | 44 / 16 % | 71 %, 8219 MiB |
  | e2e-distributed-1 | 93 s | 79 s | 76.8 s | 49 / 5 % | 21 %, 8098 MiB |
  | e2e-distributed-2 | 93 s | 76 s | 73.3 s | 50 / 3 % | 20 %, 8178 MiB |
  | mean | 88.5 s | 72.5 s | | | |

  Every shard is above 65 s and the result is no better than the memory directory alone. With four
  CPUs about half busy, these runs do not show aggregate CPU saturation, so the priority change is
  reverted. The CPU diagnostic stays.
- 2026-10-21: Working hypothesis, not yet demonstrated: each load is bounded by its own layer
  decompression. `podman save --format oci-archive` writes gzip layers by default, the store
  receives about 3.5 times the archive bytes, and the backend takes 45–58 s even alone. Controlled
  comparison, temporary and removed before merge: in the `container` domain only, after the normal
  loads, the loader re-exports the largest image with `podman push --compression-format zstd` and
  loads the gzip archive and the zstd archive one after the other, each alone into a fresh store
  under `/var/tmp`. It prints the layer media types, the archive sizes, the export time and both
  solo load times. A failure only prints a notice. The E2E load steps do not run it. Changing the
  CI or release archive format remains an owner decision (posted on PR #323).

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-705.
