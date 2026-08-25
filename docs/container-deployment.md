# Final container image

T20 builds the final UBI 9/Python 3.14 image with `scripts/container/build.sh`. The build pins the
base manifest, every downloaded engine archive, the Google signing key and Chrome RPM signature,
the Mermaid npm graph, the `uv` binary, the Python lock, and the complete resolved RPM inventory.
Use `SOURCE_DATE_EPOCH` only when intentionally producing a new reviewed image identity; the
default is fixed for reproducibility.

The entrypoint accepts exactly `api`, `embedded-worker`, or `external-worker`. `api` serves HTTP
without a worker. `embedded-worker` is the one-replica standalone process. `external-worker` is a
distributed worker without HTTP. Both worker modes use the package-native `md_converter.runtime`
assembly and the same production conversion processor.

Every mode refuses UID 0, non-empty effective or bounding capabilities, absent
`no-new-privileges`, a writable root, or missing dedicated `/tmp`, `/work`, and `/dev/shm` mounts.
Run as an arbitrary platform-assigned UID in group 0. Never grant a capability, privileged mode,
host networking, an unconfined seccomp profile, or Chrome's `--no-sandbox` flag. `/tmp` and
`/dev/shm` are bounded memory volumes. `/work` is a bounded disk-backed ephemeral volume in the
deployment examples. `/data` is mounted only for standalone persistence.

## Profiles and resource policy

`deploy/standalone.yaml.example` runs one `embedded-worker` replica with a ReadWriteOnce `/data`
claim. `deploy/distributed.yaml.example` separates API and external-worker deployments and expects
PostgreSQL plus an AWS S3-compatible store. RustFS is the test implementation in
`deploy/rustfs-ci.yaml`; there is no RustFS-specific application API.

The examples deliberately contain `${...}` placeholders. Render them only after supplying every
approved value. `WORKER_MEMORY_BUDGET_BYTES` and `WORKER_EPHEMERAL_STORAGE_BUDGET_BYTES` appear both
in the application configuration and the worker resource limits so the T18 values are enforced
without unit conversion. Do not introduce example values as production defaults. The Localhost
Chrome seccomp profile is `spikes/toolchain/chrome-seccomp.json`; install that exact reviewed file
on each worker node before applying a manifest. OpenShift validation remains deferred, so these
manifests do not claim OpenShift compatibility.

ClamAV is an external `clamd` service selected through `MD_CONVERTER_CLAMAV_HOST`, port, and timeout.
Network policy must permit only the required clamd, PostgreSQL, S3-compatible, DNS, and inbound HTTP
flows. Scanner unavailability remains fail-closed; the application keeps no durable quarantine.
Credentials and the initial administrator password belong in a Secret, never a ConfigMap or image.
The image also requires explicit conversion limits for archive compression, image dimensions and
SVG structure, Mermaid source/output dimensions, PDF structure, and cancellation polling. Configure
the locked `mmdc`, Chromium, Pandoc, LibreOffice, and font-manifest paths; none of these limits is an
implicit production recommendation.

## Verification and supply chain

Run:

```bash
bash scripts/container/build.sh localhost/md-converter:t20
bash scripts/container/smoke.sh localhost/md-converter:t20
bash scripts/container/api-smoke.sh localhost/md-converter:t20
bash scripts/container/distributed-api-smoke.sh localhost/md-converter:t20
bash scripts/container/supply-chain.sh localhost/md-converter:t20 artifacts/container
```

The smoke harness uses real rootless Podman, an arbitrary UID supported by the host subordinate-ID
map, the reviewed Chrome seccomp profile, read-only root, no capabilities, `no-new-privileges`, and
bounded cgroups and writable areas. It executes Pandoc, sandboxed Mermaid/Chrome, and LibreOffice.
The standalone smoke starts the embedded worker on the image's SQLite 3.34 runtime and performs
real authenticated template publication plus DOCX, PDF, and combined asynchronous conversions. It
polls jobs, inspects OpenXML/PDF/combined bytes and canonical traceability sidecars, and verifies
source mismatch and forbidden-suffix failures. The distributed smoke repeats that workflow through
PostgreSQL, RustFS, a worker-free API, and an external worker; it also verifies the worker metrics
listener and a clean SIGTERM exit. A protocol-faithful clean-only INSTREAM sidecar permits positive
uploads; dedicated tests retain clean, malware, unavailable, and malformed ClamAV coverage. Full
three-user, two-profile recovery and concurrency E2E remains T21 scope.

The supply-chain command downloads SHA-locked Syft and Grype releases, writes CycloneDX and SPDX
JSON SBOMs, retains the complete fixed and unfixed Grype report, and separately fails on Critical
findings with an available fix. Unfixed Critical findings remain explicit and require mitigation or
rollback evidence before release; they are never silently filtered. CI retains both SBOMs, the
report, and bounded image metadata/digests for 30 days as verification evidence. Scanner databases
remain time-varying evidence. T22 owns release publication, provenance, and registry attachment.

## Production conversion runtime

The processor loads the frozen owner-scoped source and the exact verified template version, verifies
the persisted filename, kind, size, and digest without inferring source type from content, and
delegates validation, Mermaid rendering, DOCX creation, and PDF creation to the existing bounded
adapters. PDF traceability is published as a canonical sidecar; `both` output is also a
deterministic ZIP containing `document.docx`, `document.pdf`, and `traceability.json`. Embedded
worker failure makes standalone readiness fail. External workers expose process-local metrics and
stop on SIGINT or SIGTERM.

The UBI runtime provides SQLite 3.34.1. SQLite mutations acquire `BEGIN IMMEDIATE`, execute their
conditional update, and select the result in the same transaction; PostgreSQL keeps
`UPDATE ... RETURNING` and `SKIP LOCKED`. Tests install a SQLite 3.34 grammar guard so any emitted
`UPDATE ... RETURNING` fails even on newer developer SQLite libraries.

Build-only npm tooling and unused curl, OpenSSL, and Apache HTTPD command-line executables are
absent from the runtime filesystem. Required shared libraries and the complete RPM inventory remain
recorded. This reduces unused attack surface without altering the vulnerability threshold.
