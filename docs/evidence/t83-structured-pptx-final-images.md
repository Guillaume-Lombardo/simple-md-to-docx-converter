# T83 structured PPTX final-image evidence

This page records the terminal local candidate run. It does not authorize publication, deployment,
or completion. The retained operator bundle is identified as `t83-final-b186d3a-attempt3`; its OCI
archives, SBOMs, scan reports, measurements, logs, and checksums are retained with the qualification
evidence.

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

T83 remains In Progress. Exact-head and exact-main checks, release/publication decisions, and any
T73 stacked integration or deployment qualification remain separate. No public image, version,
registry digest, or `main` completion is claimed here.
