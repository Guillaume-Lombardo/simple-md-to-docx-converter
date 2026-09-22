# T83 structured PPTX final-image evidence

This page records the terminal local candidate run. It does not authorize publication, deployment,
or completion. The retained operator bundle is identified as `t83-final-b186d3a-attempt3`; its OCI
archives, SBOMs, scan reports, measurements, logs, and checksums are retained with the qualification
evidence.

## Qualified local candidate: `4888cd0`

A later local candidate was built from clean source
`4888cd067e848c59162d801c2399be99b7f81969`. It preserves the historical `e3fb99b`
identities below rather than relabelling them. The candidate images are local tags, not registry
publication receipts or deployment pins.

| Image | Local candidate tag | Configuration ID | Image digest |
| --- | --- | --- | --- |
| Backend | `localhost/md-converter:t83-final-4888cd0` | `e7c53afc1d239e06bd2f907e7dd5b372b931cda2f06702fcaf4b53ad8c36861b` | `sha256:95e014381d856504a3bc6e4c6dca4cdd4e6abf30aefacd5fbc47d7a8b6c6cc6b` |
| Frontend | `localhost/md-converter-web:t83-final-4888cd0` | `da476b2628a89c92c935fa877b028f1fdf83696f177976f9702a4bb5135f9e95` | `sha256:f788da013c5290f110175cb6c05e4078b74cdb52628a13a4a164571fdfccbafc` |
| Reverse attempt | `localhost/md-converter-reverse-attempt:t83-final-4888cd0` | `4d1b9bd322ed8b058e6c3d1778224a81475f447e1aebe36416b252c90b646ee5` | `sha256:518e9f2cd71e999ed5e719ccdf3d554f1505d97ce925830eff684ec8aeb322f8` |

Preflight and 13 focused boundary tests passed, followed by the 28-mutant campaign, the
three-image build, and an uncompressed OCI export of the reverse runtime. An initial run stopped
when Podman could not unpack a layer after the host filesystem filled (`ENOSPC`); its rollback/pull
failure remains retained diagnostic evidence. The corrected attempt changed only the verified disk
prerequisite and artifact offload, leaving runner and source guards unchanged. It completed both
standalone and distributed workflows, 21 resource measurements, and all three CI-mode supply-chain
scans. Independent ASTRA review approved the source/runner guards and recorded checksums.

The scans recorded zero Critical findings. High counts were 133 for the backend, 39 for the frontend,
and 134 for the reverse attempt. Embedded reverse Cargo evidence recorded zero Critical and High
findings. The reverse runtime manifest above is derived from the uncompressed OCI export. The
compressed scan archive manifest
`sha256:4977529c5c86a3ab01c1718732303f265056893196cc037495d938db182d1106` is evidence for that
scan archive and is not a runtime identity.

## Docker-box deployment

The qualified candidate is now deployed to the Docker-box test instance and its public HTTPS endpoint
`https://markweave.g1lom.xyz`. The active backend configuration ID is `e7c53afc`, frontend
configuration ID is `da476b26`, and reverse runtime manifest is `sha256:518e9f2cd71e999ed5e719ccdf3d554f1505d97ce925830eff684ec8aeb322f8`, matching the qualified candidate. Four services are
healthy, the broker is active, schema 19 is present, and no job was active before cutover.

Backup `69bd3ba43e9bc1ed77509f4d06f7d383058fe4591a41c9adb8f329478a0f4ed7` was independently verified
from a host copy; the previous virtual environment and configuration remain available for rollback.
An authenticated structured Slides smoke job `000fc13e-fda2-417e-b338-521e0a584324` succeeded with a
1,572-byte result, SHA-256
`317f4e6c13f6c6bfec17af623bfa3670472d3b6c802f8138ac418eb556acf528`, and validation of notes,
images, and warnings. Both reverse READY metrics were 1, the smoke session logged out, the final
smoke helper exited 0, and router/public readiness passed.

The original cutover script exited 1 after backend/frontend update because fixture transfer failed at
`docker cp`/tmpfs. That receipt remains failed; it is not relabelled as successful. After fixture staging through tar stdin, the independently approved final smoke helper passed
without changing the candidate.
An earlier backup-creation host-UID failure stopped safely and reopened the old healthy service before
the independently approved default-image-user correction. This is a test deployment, not a public
release, `main` integration, or T83 completion.

## Identity and execution

The three final candidate images were built from application source `e3fb99be5763bb8fc6f100be43140e61e221f82d`.
The tracked E2E harness and final test-only corrections were run from clean source head
`b186d3ac3718a71205196b24e0a60f72455ee8ee`. The harness guards recorded the source state before and
after every stage; the application build-input diff was empty, so the two final harness corrections
did not rebuild application images. The image identities were recorded in the retained bundle:

| Image | Local candidate tag | Configuration ID | Image digest |
| --- | --- | --- | --- |
| Backend | `localhost/md-converter:t83-final-e3fb99b` | `5a81e96cdb9008e673e95987e184fbe3b9040c927d39ba6be5d1b257f5b906d7` | `sha256:0f1f1ffd2872705bff960e1fe13ec5e79e54b73a9d989e706b1ff832e320ad58` |
| Frontend | `localhost/md-converter-web:t83-final-e3fb99b` | `bdfe119451ff622a765fb18f9e458a26a4316619768c77b58ecb4716a734bc1b` | `sha256:5205e27a150c0c91b26deb80d6794f24fd03e3e750c756aa58d32c61eed67a2f` |
| Reverse attempt | `localhost/md-converter-reverse-attempt:t83-final-e3fb99b` | `ea8c8d57ffc7aa837862d21454ece8b8ab4358cd4a67a60a35fef21eb17116ae` | `sha256:f44f0237474f1bed1b36f264a8e217ac63b72f64e76740e156462667bb9ea15b` |

These are local candidate identities, not public registry publication receipts or deployment pins.

## Full profile results

The terminal run completed both profiles with exit 0 against the same three-image set. Structured
PPTX API, installed CLI, and browser Marp workflows passed, including authoritative options,
capability-derived controls, exact submitted options, and downloaded package inspection. The broader
profiles also exercised the existing reverse API/CLI/browser lifecycle, eight-family corpus,
owner/authorization and scanner failures, cancellation, expiry, unavailable backend, worker and
broker crash recovery, frontend outage, and recovery download verification. The run retained the
expected fake-ClamAV teardown SIGKILL warning; it did not change either profile's exit status.

The harness SHA-256 is
`9e5e4ec08763fdcdf796fb230a1986ebd51be159b6fad4874224459ac2749eb5` for `scripts/e2e/run.sh`.
Standalone, distributed, resource measurement, and all three supply-chain stages each recorded exit
0. Independent review verified the E2E and resource evidence, image identities, all bundle checksums,
scan results, and the role-specific Cargo evidence.

## Resource and supply-chain evidence

The retained reverse-attempt measurement contains 21 resource cases plus cancellation and the T69
in-process concurrency comparison. It records CPU, wall time, RSS, threads, and containment
observations under the tested image. The artifact explicitly states that no numerical production
performance threshold is approved; these measurements do not establish one and do not qualify
production worker admission.

CI-mode scans retained zero Critical matches and High counts of 133 backend, 39 frontend, and 134
reverse-attempt. The embedded anydoc Cargo scan also recorded zero Critical or High matches. The
images are not described as vulnerability-free. Complete per-image OCI/SBOM/vulnerability/license
bundles and checksum manifests remain in the retained operator bundle.

## Boundaries

The canonical engine-excluded run passed at exact source
`0980498b8a5ac6db575c09c99fe0814794a518a9`: 4,674 passed, 56 engine-marked tests deselected, and
11 warnings, with 94.96% overall coverage, 91.17% branch coverage, and 97.07% changed-line
coverage. This is distinct from the final-image source and harness heads above. The 56 engine-marked
tests still require the applicable CI/environment checks.

At this historical qualification stage, exact-head and exact-main checks, release/publication
decisions, and T73 integration remained separate. These candidate receipts do not establish public
publication or main verification; subsequent results are recorded below.

## Subsequent local validation

At clean local snapshot `984da696d0ad2c8b0810414037a19bd65f967bb4`, the normal T73 merge was
independently approved. The only application delta from the prior `912926ec46b25f135c93377d0cae885d3cd29fe7` snapshot was a reviewed
12-line CLI deadline fix; PPTX behavior is unchanged, while the CLI correction changes backend image inputs. `uv sync --all-groups`, global Ruff
and `ty`, 255 focused CLI/CI policy tests (18.14 seconds), and the clean release-install test
(15.93 seconds) passed.

The actual 28-mutant campaign killed all 28 selected mutants in 55 seconds, with all six failure
statuses at zero and clean exact-head/status guards before and after. Its retained operator receipt
is `t83-mutation-all-20260922T003120Z`; the report SHA-256 is
`bbe887d9d520c0dd83f87bb9152bc5c7183173998e04657f38889d0d697103e0`. No fresh image was built from
this snapshot, and these checks do not relabel the earlier `e3fb99b` images or establish deployment,
publication, or current-main integration. Exact PR/main CI remains pending; the full main `582886e5c87799b6de19a5fc3c0369916055f7a0`
artifact rerun awaits authorization.

## Isolated test deployment

On 2026-09-22, the same candidate application and native broker source was deployed to the existing
docker-box test instance. This is not a public release: package version remains 0.7.1. The three
runtime image identities above were verified, all four Compose services were healthy, and public
HTTPS readiness passed. SQLite migration `20260921_19` and `quick_check` passed after a complete
pre-migration database/object backup and verification of its independent host copy.

An authenticated structured Slides job succeeded on that host. The shared validator checked the
edited slide order/text, presenter note, normalized image pixels, unsupported-content warning, and
package manifest. Broker and reconciliation READY metrics were both 1 before ingress reopened.
The smoke session was logged out. The earlier interrupted synthetic job also completed after
recovery; no user data was restored or directly edited.

Transport required a Docker archive for the backend/frontend and an uncompressed OCI archive for
the reverse runtime to preserve its exact manifest digest. An initial compressed import exposed a
Podman cache distinction between image lookup and the digest actually bound to a created container.
The latter was checked before accepting the final deployment. The failed never-started creation was
recovered through the existing validated runtime adapter and normal broker reconciliation after
independent review of exact identity, provenance, historical events and runtime absence. No broker
inventory row was edited and no digest check was relaxed. Operator receipts retain the complete
failure and recovery sequence; this is not a general recovery procedure for unknown execution
histories.

## Inventory snapshot correction and forthcoming release

On 2026-09-22, PR #259 CI `35705172160` passed the corrected capabilities smoke and all other
jobs except standalone E2E and its dependent gate. Independent diagnosis identified a real
inventory read race: autocommit could compare rows and their authenticated manifest from different
committed states. The retained traceback matches that branch, but missing database artifacts do
not prove the exact historical interleaving.

The reviewed correction uses one explicit read transaction across verification and returned data,
including constructor verification. All integrity checks and writer transactions are preserved.
The 45 inventory and real-SQLite tests passed; an independent negative control made all three new
concurrent-writer regressions fail without the snapshot. This changes application source: the
historical image receipts above are not evidence for the corrected candidate. New exact-head CI
and final-image qualification must pass before completion.

The user authorized T87 to validate documentation, publish patch 0.7.2 through the existing protected
workflow, and adopt the exact resulting public receipts. Publication has not occurred. The existing
qualified docker-box deployment remains available while the correction is validated.

## Verified PR and main integration

PR #259 merged as `41329266140ce0a49a74cc8eab559e07a03df605` from reviewed head
`01f8c3e68a940187638916709baaf6ca8167687e`. CI `35713604587` passed every check, including
both complete E2E profiles and document engines. Its actual changed-domain mutation report selected
and killed 14 mutants, with all six failure statuses zero.

The final local canonical suite passed 4,698 tests, with 56 engine-marked tests deselected and
11 warnings, in 1,964.42 seconds. Overall coverage was 94.98%; exact source guards passed before
and after the run. The previous two local systemd failures were traced to the host disk-cleanup
service deleting an unused test image. Fixture-owned never-started containers now protect the four
required images until teardown. All 33 focused process integration tests passed, including real
partial-creation cleanup regressions; no retainer remained and the external image was preserved.
The failed run remains diagnostic evidence, not a successful qualification.

Main CI `35716730560` passed every check at the exact merge commit, including both complete
E2E profiles, both storage suites, frontend, functional and document-engine validation. T87 will qualify and publish the final versioned three-image set
through the protected release workflow before adopting the actual registry receipts and updating
the docker-box test deployment. The local candidate identities above remain historical.

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
