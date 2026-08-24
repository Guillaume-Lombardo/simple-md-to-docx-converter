# Final container image

T20 builds the final UBI 9/Python 3.14 image with `scripts/container/build.sh`. The build pins the
base manifest, every downloaded engine archive, the Google signing key and Chrome RPM signature,
the Mermaid npm graph, the `uv` binary, the Python lock, and the complete resolved RPM inventory.
Use `SOURCE_DATE_EPOCH` only when intentionally producing a new reviewed image identity; the
default is fixed for reproducibility.

The entrypoint accepts exactly `api`, `embedded-worker`, or `external-worker`. `api` serves HTTP
without a worker. `embedded-worker` is the one-replica standalone process. `external-worker` is a
distributed worker without HTTP. The latter two modes require the package-native
`md_converter.runtime` assembly described below; they fail closed until that module lands after the
T19 source freeze.

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
The API smoke exercises standalone SQLite/filesystem readiness in the final image. Full three-user,
two-profile conversion/recovery/concurrency E2E remains T21 scope.

The supply-chain command downloads SHA-locked Syft and Grype releases, writes CycloneDX and SPDX
JSON SBOMs, and fails on fixed Critical findings. Scanner databases remain time-varying evidence;
retain the JSON report with the image digest and scan timestamp. T22 owns release publication,
provenance, and registry attachment.

## Package-native runtime change still required

After T19 releases application source, T20 needs one `src/md_converter/runtime.py` module and focused
tests. It must compose the existing public `build_components`, `create_app`,
`build_embedded_worker`, and `build_external_worker_loop` factories with a production
`TemplateAwareProcessor`. That processor must load the owner-bound source through `ObjectStore`,
validate `.md` or `.zip`, run Mermaid/Pandoc and optional LibreOffice with the configured T18
budgets and cancellation deadline, and publish DOCX, PDF plus traceability, or the deterministic
combined ZIP. It must attach embedded-worker start/stop/failure to FastAPI lifespan and handle
SIGTERM for external workers. No duplicate image-only implementation is acceptable.

The UBI runtime exposes SQLite 3.34.1, while current developer tests use a newer library. The first
standalone final-image startup proved that SQL `RETURNING` is rejected and exposed 14 affected
statements. Preserve PostgreSQL `RETURNING`, but implement atomic SQLite-compatible fallbacks for:

- jobs: `activate_source`, `request_cancel`, `claim`, `heartbeat`, both updates in
  `recover_expired_leases`, `expire_terminal`, `complete_cleanup`, and both branches of
  `_finish_owned`;
- users: `commit_verified_login` and `update_security`;
- templates: `claim_stale_pending` and `_cas_update`.

Focused tests must exercise every success, stale/fencing miss, and concurrent ownership behavior on
SQLite 3.34, then repeat the standalone rootless API and queue smoke in this image. Do not replace
UBI's SQLite or weaken PostgreSQL locking to avoid these changes.
