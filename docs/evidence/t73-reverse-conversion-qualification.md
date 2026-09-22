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

The clean canonical run at `27eba5082bb299f343ab3ab49420dbea65e7d9e3` completed with 4,525 passed,
56 engine-marked tests deselected, and 11 warnings in 1,662.28 seconds. Coverage was 94.97%
overall and 91.23% branches (6,408/7,024), with 100% changed application coverage (15/15) from
base `582886e5c87799b6de19a5fc3c0369916055f7a0`. The earlier `5f90757` stale harness-order assertion is retained as history; the full
run now closes that failure. A local normal merge of reviewed T48 head `1bb4ca1b55588dc7b254d2a006b3d2df764a1056`
was independently approved with 207 policy tests and global static checks, with no application
source diff against the canonical result. This local adoption does not imply T48 is on `main` or
that PR #253 is complete.

The user selected qualification of the existing administrator surfaces. The criterion therefore
requires owner-only, non-enumerating reverse routes with administrator denial, content-free
operational metrics, and the existing authorized administrator surface exposing immutable,
content-free audit records. It does not require a new reverse-job administrator view. The existing
final-image evidence covers the owner/admin route denial and content-free operational behavior
recorded by the current E2E workflow; no new endpoint is inferred here.

The actual mutation campaign completed in 55 seconds at clean source
`6fb3f701bfacbc151850055ae2ceaac16b8863cb`, with unchanged source guards before and after.
All 28 mutants were killed; all six failure statuses were zero. The six domains selected
4, 5, 5, 6, 5, and 3 mutants. The retained report SHA-256 is
`bbe887d9d520c0dd83f87bb9152bc5c7183173998e04657f38889d0d697103e0`.
The generated report is retained in the operator evidence bundle, not committed.

At that qualification stage, remaining gates were exact final PR/main CI, including the 56 engine-marked cases, and verified
main integration. T50 and T85 closure mirrors and the T74 Backlog mirror are included in surrounding
tracking scope. Public image publication had not yet been authorized.

## Main verification and release follow-up

PR #258 merged as `31f19243ebe25345dec2c3bde843a5caf261d53a`; exact-main CI `35702469912`
passed every check, including both complete reverse E2E profiles and document engines. The user
subsequently authorized T87 to publish patch 0.7.2 with the matched three-image set and adopt its
actual public receipts. T73 remains In Progress until that public-image criterion is verified.

The later inventory snapshot correction and fixture-image retention fix merged through PR #259
as `41329266140ce0a49a74cc8eab559e07a03df605`. Its exact-head CI `35713604587` passed all
checks and both profiles; main CI `35716730560` also passed every check. These newer checks do not relabel the
historical local images recorded above.

## Failed 0.7.2 image qualification and approved continuation

Release preparation PR #260 merged as `b28256486af4cf6d58c3aa06a27815c321df57f9`.
PR CI `35719688632` and main CI `35723245238` passed every check. Automatic release
`35723245369` published PyPI and the final tag/Release at that source, and an isolated public
Python 3.14 installation passed import/version/CLI verification. Its complete standalone
release-image qualification passed. Distributed broker-restart qualification failed because the
fault-injection observer paused a new synthetic unit before binding the exact recovery attempt.
The strict guard rejected the mismatch before crash injection; the exact interleaving is unknown.

GHCR publication and staging-artifact retention were skipped, leaving no exact image bytes for
recovery. These images are not a qualified public release. The user approved a targeted harness
correction with regressions and independent review, followed by a narrowly verified `0.7.3`
continuation. Existing public `0.7.1` pins and historical candidate identities remain unchanged
until actual `0.7.3` publication, adoption and deployment are verified.

## Successful 0.7.3 public image publication

Main CI `35747016968` passed at source `84e35fed521c61d34823d18767f47eb87253d4eb`.
Protected release run `35747017469` then completed successfully and published PyPI plus one matched
schema-2 three-image set from that source. The verified public registry manifest digests are:

| Role | Published `0.7.3` digest |
| --- | --- |
| Backend | `sha256:c91f97d7c299ad84811876e52ae52350d1ad3b91bd190b4fffb801634729bc83` |
| Frontend | `sha256:edee507cf70d15681bae0fe7f9d0755b607d557725fd06e6bd43771350dba9ba` |
| Reverse attempt | `sha256:39f4a68358029977b6ec5ac6cb26fab88ac10e518016b42e8389abd54f517e61` |

Anonymous requests for both the `0.7.3` and `source-84e35fed521c61d34823d18767f47eb87253d4eb`
tags returned these exact digests for all three repositories. This satisfies T73's public third-image
publication criterion. T73 remains In Progress until the repository adopts the receipts and the
docker-box deployment is updated and verified; the local candidate identities above remain historical.
