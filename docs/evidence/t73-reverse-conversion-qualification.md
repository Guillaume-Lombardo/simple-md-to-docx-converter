# T73 reverse-conversion qualification evidence

This records final-source local image qualification, not a public release. The retained operator
bundle `t73-final-c6ff8b1` includes source guards, build and profile logs, resource measurements,
image identities, SBOMs, scan reports, and per-image checksum manifests.

## Source and image identity

All three images were built from clean source commit
`c6ff8b1e3913eda1c8103260d37bce00ca106567`. Each image's local tag, image configuration ID, and
Podman image digest were recorded by the guarded build workflow:

| Image | Local candidate tag | Configuration ID | Local image digest |
| --- | --- | --- | --- |
| Backend | `localhost/md-converter:t73-final-c6ff8b1` | `6aec8db4cc57f7efa6824d69273735e0fd21e5030c66127bd4c0ad662264e18f` | `sha256:c5961861fa7b8dee9742d25e1bc90ce94eb30c9c73cba8cb376ce67e9663a3ac` |
| Frontend | `localhost/md-converter-web:t73-final-c6ff8b1` | `0fea3036bc18055f2c7d3fef9dc044efc1cecfa4c6fa992eebbfc912168870b4` | `sha256:2927d7ee527696f29d8ad5cd3cdcc95a38c9333a261d17829a5aab465a83ec66` |
| Reverse attempt | `localhost/md-converter-reverse-attempt:t73-final-c6ff8b1` | `5454313825c6ce30c2e995362d4616d3848a331c01fab207fcc153dda959b322` | `sha256:715c8e8e305785323d7da9309f8e2a09d6b19a1b64c4ea7e572fd9caa3636011` |

These are local candidate identities, not registry digests or publication receipts. No public version
was selected, and no third image was published or pinned. The existing published 0.7.1 backend and
frontend pair remains unchanged.

The tracked runner was unmodified: `scripts/e2e/run.sh` SHA-256 was
`0035610454467244a1390a7462f9fc605fa9eb95c532d657abf260fb9742b675`. The harness checked the clean
expected source head before and after each stage, with primary-only mode unset.

## Full final-image profiles

The unmodified tracked runner completed both profiles against the exact image set above:

| Profile | Start (UTC) | End (UTC) | Exit |
| --- | --- | --- | ---: |
| Standalone SQLite/filesystem | 2026-09-21 20:05:32 | 2026-09-21 20:15:48 | 0 |
| Distributed PostgreSQL/S3 | 2026-09-21 20:15:48 | 2026-09-21 20:26:40 | 0 |

Both runs exercised the tracked forward workflow and the reverse API, installed CLI, browser, and
corpus workflows; capability and admission behavior; quota and cancellation; expiry and unavailable
backend behavior; worker and broker crash recovery; frontend outage; and recovery to a browser
download whose digest matched the retained result. The reverse corpus covered the eight approved
format families. Logs also record the expected content-free authorization, scanner, OCR, and
unsupported-input paths. Neither profile encountered the earlier bounded CLI wait race.

Both logs contain a fake ClamAV container shutdown warning: SIGTERM did not stop that test service
within ten seconds, so teardown used SIGKILL. The profile workflows still completed with exit 0; the
warning is retained rather than presented as a clean shutdown.

An independent reviewer verified the runner hash against the committed runner and retained receipt,
the source-clean guards and unset primary-only selector, both full profile exits, stage timestamps,
image identities, and the held-queue, quota, cancellation, SIGKILL-137, and restored-result-digest
evidence. Review approval covered final-image E2E and resource evidence only; it did not
independently audit the supply-chain reports or close the remaining mutation, engine-marked CI, or
administrator-criterion work below. All three image supply-chain scan stages nevertheless completed
with exit 0, with findings summarized separately in this page.

## Resource measurements and image supply chain

The final reverse-attempt image measurement artifact contains 21 format/resource cases, five
measurements per case, cancellation stress, and the existing T69 in-process concurrency comparison.
Across the 105 format samples, CPU time ranged from 0.015 to 6.904 ms and wall time from 0.014 to
7.043 ms; the largest per-case peak RSS was 41,824 KiB and the maximum case thread count was two.
The 2,800,210-byte cancellation stress recorded 431.303 ms CPU, 473.514 ms wall, 230,296 KiB peak
RSS, and three peak threads. The concurrency comparison ran 25 PDF conversions per worker count:

| Workers | Batch CPU | Batch wall | Process peak RSS | Peak threads |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 159.635 ms | 159.016 ms | 41,824 KiB | 3 |
| 2 | 324.009 ms | 288.387 ms | 43,560 KiB | 4 |
| 4 | 693.002 ms | 683.293 ms | 47,784 KiB | 6 |

The measurement harness reports one CPU, 256 MiB memory, 16 PIDs, 32 MiB workspace, UID 12345,
zero effective capabilities, no-new-privileges, and loopback-only networking. The artifact reports
that production numerical performance thresholds have not been approved. These are retained
measurements, not a pass against an invented CPU, memory, or throughput threshold; the concurrency
comparison is not proof of production worker admission.

CI-mode Grype reports recorded zero Critical matches and these High-severity match counts:

| Image scan | High matches | Critical matches |
| --- | ---: | ---: |
| Backend | 133 | 0 |
| Frontend | 39 | 0 |
| Reverse attempt | 134 | 0 |
| Embedded anydoc Cargo graph | 0 | 0 |

The images are not described as vulnerability-free. Each image bundle retains its OCI archive,
metadata, CycloneDX and SPDX SBOMs, vulnerability report, and `release-bundle.sha256` inventory. The
SHA-256 values below identify those complete inventory files:

| Bundle | SHA-256 of `release-bundle.sha256` |
| --- | --- |
| Backend | `922f61f833a78f64467f4212a9f0063146b9ded58afa2844c6391aa9a9fa3373` |
| Frontend | `868fe8d4702279602212d9283122d9b3cac5242f8676692fa18c25aff06a0dad` |
| Reverse attempt | `bfa0f3f21c272e68bf951aa02318070f63637611eeb0f29c537a52fe3241238c` |

The local evidence stages for resource measurement and all three image supply-chain scans exited 0.
Scan output and bundle members remain in the retained local/operator evidence bundle.

## Local canonical checks and remaining work

At source `5f90757aeadd4d15273ae6524379804f099f3781`, the canonical full Python invocation reported
4,520 passed and one failed assertion: a CLI harness-order test expected one invocation after the
intentional held-queue phase added a second. It also reported 56 engine-marked tests deselected and
11 warnings. The full invocation therefore did not pass. The test was corrected to assert the normal
and held-queue invocation contexts independently. At final source `c6ff8b1`, all 44 focused CLI and
harness tests passed, and global Ruff format/check and `ty` checks passed. Coverage from the
canonical run was 94.97% overall, 91.23% branch coverage (6,406/7,022), and 100% of changed
application lines (15/15).

T73 remains In Progress. The literal administrator operational-metadata acceptance criterion still
needs the user's decision; no criterion change, waiver, or administrator reverse-job view has been
made. The reviewed mutation manifest now contains 28 mutants, but the actual mutation campaign has
not run. The 56 engine-marked tests remain unverified locally, and exact-head PR CI and exact-main
checks remain pending. Public image publication and release qualification remain outside this
evidence and require the separately authorized release work.
