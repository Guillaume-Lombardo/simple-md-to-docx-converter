# T00 document toolchain validation

## Status

Pandoc, Fontconfig, and LibreOffice are compatible with UBI 9 and Python 3.14 in
a repeatable compatibility probe. Mermaid CLI is installed, but Chrome cannot
render under the intended security profile. The approved follow-up is a minimal
seccomp/user-namespace profile validated on rootless Podman and then k3s, with
the Chrome sandbox retained and `--no-sandbox` forbidden. Do not use this probe
as the production image and do not enable Pandoc `--sandbox` for conversions
with local resources.

This report records evidence observed on August 23, 2026. The base image and
engine archives are pinned. RPM dependencies resolved from the mutable UBI
repositories are not pinned or snapshotted, so rebuilding later can produce a
different package set. This is not a reproducible-image claim.

The detailed source, signature, license, update-ownership, Chrome-sandbox,
CommonMark, and font evidence is in `docs/evidence/t00-decision-matrix.md`. It
records the PM-approved choices and the remaining validation gaps.

## Reproduce the evidence

Requirements:

- Docker with BuildKit and permission to run containers;
- outbound access during the image build to pinned engine URLs and mutable UBI
  repositories;
- a Linux host with cgroup v2 and support for container seccomp options.

Run the successful document-engine and security-property probe:

```bash
spikes/toolchain/run-validation.sh documents
```

Run the successful probe and all expected-failure probes:

```bash
spikes/toolchain/test-validation.sh
```

The test script verifies:

- Python 3.14 runs as an arbitrary non-root UID;
- `/proc/self/status` reports zero effective and bounding capabilities and
  `NoNewPrivs: 1`;
- a write to the group-writable image path `/opt/app-root/src` is rejected by the
  read-only root mount;
- only the loopback interface is visible and the explicit `/tmp`, `/work`, and
  `/dev/shm` mounts have their required bounded tmpfs options;
- memory, CPU, and process count are bounded by cgroups;
- Pandoc produces a valid DOCX with an embedded local PNG and a reference DOCX;
- Pandoc `--sandbox` cannot resolve that local resource, reports the failure, and
  produces OpenXML with no embedded media part;
- Chrome fails deterministically without rendering Mermaid under the intended
  security profile;
- Fontconfig resolves DejaVu Sans and creates its cache below the arbitrary
  user's writable XDG cache;
- LibreOffice uses an isolated user profile and produces a PDF from the DOCX;
- root execution, non-empty capabilities, missing `no-new-privileges`, writable
  root, attached network, wrong arbitrary UID, missing bounded work storage, and
  the intended Chrome target profile fail deterministically.

`run-validation.sh` accepts probe-only resource controls through
`TOOLCHAIN_MEMORY`, `TOOLCHAIN_CPUS`, `TOOLCHAIN_PIDS`,
`TOOLCHAIN_WORK_SIZE`, and `TOOLCHAIN_TMP_SIZE`. Their defaults (2 GiB, 2 CPUs,
512 processes, 1 GiB, and 512 MiB) are probe envelopes, not product defaults.
The probe prints cgroup memory/process peaks and final `/work` usage so T18 can
use measured corpus results when it defines the real budgets.

Set `TOOLCHAIN_WORK_STORAGE=disk` to use a dedicated disk-backed bind mount for
`/work`. This proves engine compatibility with disk storage, not a bounded
ephemeral-volume policy.

The tiny document fixture used 111,779,840 bytes at the cgroup memory peak,
8 processes at the cgroup PID peak, and 884 KiB in `/work` during the recorded
successful run. These values describe this one probe only.

## Pinned inputs and resolved RPM inventory

| Component | Pinned evidence | Source status |
|---|---|---|
| Base image | `registry.access.redhat.com/ubi9/python-314@sha256:194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653` | Red Hat UBI public registry |
| Base runtime | RHEL 9.8, Python 3.14.5, Node.js 22.23.1 | Included by the pinned image |
| Pandoc | 3.10.2, SHA-256 `c7edd535941c48be6a362081a748272837de81ae11777202d9c341d3d8261c9a` | Official GitHub release; SHA-256 accepted because no detached signature is published |
| Mermaid CLI | 11.16.0, full npm dependency lock | Official npm package; available attestation/provenance verification required for production |
| Chrome | 151.0.7922.173-1, SHA-256 `2899353cad3732b8e3a88e76996c340e047d8729ea1b881fdfdd21e0e3baefa5` | Official Google RPM; RPM signature verification required for production |
| LibreOffice | 26.2.5.2, archive SHA-256 `f62611c441ff1faa5cadb499abdbab119f5a9013eb6c0e32fc9aa65f6ff8b53d` | Official Document Foundation RPM archive; detached signature verification required for production |
| Fontconfig and runtime libraries | Resolved at build time | Mutable UBI repositories; exact result captured separately |
| Probe fonts | Resolved at build time | Mutable UBI repositories; exact result captured separately |

The public UBI BaseOS, AppStream, and CodeReady Builder repositories exposed by
the pinned base image contain Fontconfig, runtime libraries, and the selected
probe fonts. Queries for `pandoc`, `chromium`, and `libreoffice` returned no
packages. DNF installs the newest dependency versions exposed by those mutable
repositories at build time. Exact UBI RPM pinning is therefore not supported by
this harness.

The complete 552-package result of the reviewed build, including epoch, version,
release, architecture, and RPM license metadata, is committed as
`docs/evidence/t00-rpm-inventory.txt`. Its SHA-256 is
`5322961d68d22c88af7e646d50480803289d572ff8b600f2db6a6f0f0734ca2d`.
Every build also generates `/opt/toolchain/evidence/rpm-inventory.txt`; the test
checks that it is sorted, and compares it with the committed reviewed snapshot.
A different result is compatibility-review evidence, not an automatic update.

The pinned upstream engine artifacts prove technical compatibility only. The
PM approved official publisher sources, available signature/provenance checks,
locked integrity, weekly vulnerability review, and urgent handling of Critical
findings. The production build must still implement these controls and preserve
license, compatibility-regression, emergency-rebuild, and rollback evidence.

The resulting probe image was 1,119,344,981 bytes. T20 must select only the
required LibreOffice subpackages and remove build-only material before treating
image size as production evidence.

## Findings

### Pandoc sandbox

Pandoc 3.10.2 accepts the reference DOCX while `--sandbox` is active, but it
returns success with a `PandocResourceNotFound` warning and omits an image found
through `--resource-path`. The same fixed command without `--sandbox` embeds the
image successfully.

Conclusion: `--sandbox` is not feasible for the required local-resource
pipeline. T07 must use the product's independent workspace, path validation,
fixed arguments, no-network execution, deadlines, and resource limits instead;
it must retain a regression test proving that remote and escaping resources are
unavailable. The approved reader is
`commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html`,
with mandatory raw-HTML rejection before Pandoc.

### CommonMark compatibility

The automated fixture compares plain `commonmark` with an explicit
`commonmark_x` candidate. Pandoc 3.10.2 produces tables, footnotes, YAML
metadata, and image attributes for the candidate, but retains a raw HTML AST
node despite `-raw_html`. It rejects `-raw_tex` because that extension is not
supported for `commonmark_x`. Raw HTML therefore needs a separate tested policy.
The approved dialect and required pre-Pandoc raw-HTML rejection are recorded in
the decision matrix; the current fixture remains evidence for why both controls
are necessary.

### Chrome sandbox and OpenShift

With arbitrary UID `1000710000`, read-only root, no network, all capabilities
dropped, `no-new-privileges`, and Docker's runtime-default seccomp profile,
Chrome fails before Mermaid rendering:

```text
The setuid sandbox is not running as root.
Failed to move to new namespace ... errno = Operation not permitted
```

The SUID sandbox cannot elevate under `no-new-privileges`, and the namespace
sandbox cannot create the required namespaces under the target seccomp profile.
The committed probe does not use `seccomp=unconfined`,
`--disable-setuid-sandbox`, or `--no-sandbox`. OpenShift validation is deferred
by the PM; the failure remains evidence for T09 rather than an authorization to
weaken the profile.

The approved direction for T09 is to design the minimum seccomp/user-namespace
profile that keeps Chrome's sandbox active, validate it first with rootless
Podman and then with k3s, and never use `--no-sandbox`. OpenShift validation is
deferred, and support must not be claimed until the profile is proven on the
target OpenShift security context.

### Fonts and document conversion

The probe conversion resolves DejaVu Sans deterministically, creates a
per-user Fontconfig cache on writable storage, embeds the local image in DOCX,
and converts that DOCX to a valid PDF with an isolated LibreOffice user profile.
This establishes mechanics only. The approved family set is Liberation plus
Carlito/Caladea, DejaVu as fallback, and Noto only for explicitly required
scripts. T10 must still pin official artifacts, validate licenses, define the
exact substitution order and expected-template declarations, and prove the
required styles and golden rendering corpus.

### Resource budgets

The harness proves that Docker enforces configurable memory, CPU, PID, `/tmp`,
`/work`, and `/dev/shm` envelopes and exposes peak measurements. The compatibility
probe uses tmpfs for `/work` so it is self-contained. The final runtime requires
disk-backed ephemeral storage for `/work`; T20/T21 must repeat the successful
and exhaustion paths with that storage class. A single tiny fixture is not
evidence for production limits. Upload, archive, image, diagram, duration,
memory, concurrency, queue, and retention values remain unresolved for T18 and
must be measured with the T04 corpus rather than copied from the probe defaults.

## Coverage and remaining validation

The spike crosses real subprocess, filesystem, font, browser, and container
boundaries. `test-validation.sh` covers the successful Pandoc/Fontconfig/
LibreOffice path and relevant failures: all claimed container security
properties, absent bounded work storage, Pandoc sandbox resource omission in
both logs and OpenXML, and Chrome sandbox rejection under the target profile.

There is no product user-visible or operational workflow in T00, and the final
rootless application image is delivered by T20. Consequently no T00 test can
honestly be an E2E test against that final image. T20/T21 must repeat these
constraints against the final image and real OpenShift/Podman environments. If
this is treated as an E2E exception rather than a non-applicable criterion, it
requires explicit pull-request reviewer approval.

Podman and a real OpenShift cluster were unavailable in the local environment;
Docker 29.7.1 was used for the container evidence. The PM authorized system
installation of Podman on this development VM for the next rootless validation;
k3s follows Podman, while OpenShift remains deferred. The current repository now
contains the Python project and canonical quality tooling, so their results are
reported with this update rather than treated as a bootstrap gap.

The spike does not read or change a storage contract, so standalone and
distributed storage-profile parity is not affected by T00.

## Remaining validation and configuration decisions

- operational ownership and implementation of the approved official-artifact,
  available-signature, locked-integrity, weekly-review, and urgent-Critical-CVE
  policy;
- the exact minimal seccomp/user-namespace profile and its Podman/k3s proof;
  OpenShift proof remains deferred;
- exact official font artifacts, versions, notices, substitution order, and
  scripts that explicitly require Noto;
- every production resource, RPO/RTO, retention, quota, antivirus, and cleanup
  value listed in specification section 14;
- PDF/A and table-of-contents support.
