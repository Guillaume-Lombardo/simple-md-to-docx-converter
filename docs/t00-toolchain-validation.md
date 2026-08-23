# T00 document toolchain validation

## Status

Pandoc, Fontconfig, LibreOffice, and sandboxed Chrome/Mermaid are compatible
with UBI 9 and Python 3.14 in the rootless Podman probe. Docker's
runtime-default seccomp profile still rejects Chrome namespace creation. The
committed Podman profile is the containers/common 0.62.2 default with only the
`chroot` capability condition removed; k3s validation remains required. The
probe retains arbitrary UID, read-only root, no network, no capabilities,
`no-new-privileges`, explicit bounded writable areas, and cgroup limits. Do not
use this probe as the production image and do not enable Pandoc `--sandbox` for
conversions with local resources.

This report records evidence observed on August 23, 2026. The base image and
engine archives are pinned. RPM dependencies resolved from the mutable UBI
repositories are not pinned or snapshotted, so rebuilding later can produce a
different package set. This is not a reproducible-image claim.

The detailed source, signature, license, update-ownership, Chrome-sandbox,
CommonMark, and font evidence is in `docs/evidence/t00-decision-matrix.md`. It
records the PM-approved choices and the remaining validation gaps.

## Reproduce the evidence

Requirements:

- Docker with BuildKit or rootless Podman 5.4.2 with permission to run
  containers;
- `jq` when running the Podman profile-delta review probe;
- outbound access during the image build to pinned engine URLs and mutable UBI
  repositories;
- a Linux host with cgroup v2 and support for container seccomp options.

Run the successful document-engine and security-property probe:

```bash
spikes/toolchain/run-validation.sh documents
```

Docker remains the default for backward compatibility. Select rootless Podman
explicitly without a Docker alias:

```bash
spikes/toolchain/run-validation.sh --runtime podman documents
```

Run the successful probe and all expected-failure probes:

```bash
spikes/toolchain/test-validation.sh
```

Pass `--runtime podman` to run the same successful tmpfs and disk-backed probes,
inventory comparison, successful sandboxed Chrome probe, runtime-default Chrome
failure probe, profile-integrity probe, and security failure probes through
Podman. The harness checks that the selected executable exists and rejects any
runtime other than `docker` or `podman`.

Run the positive Chrome/Mermaid path directly with:

```bash
spikes/toolchain/run-validation.sh --runtime podman target
```

The runner verifies the committed profile SHA-256 before passing it to Podman.
`TOOLCHAIN_CHROME_SECCOMP_MODE=runtime-default` is retained only to reproduce
the fail-closed `sys_chroot` baseline. A modified profile or unknown mode is
rejected before the container build or run.

The image build deliberately keeps `--pull=false`. It performs no implicit pull
or store bootstrap, even though the `FROM` reference is digest-pinned. The first
Podman run therefore stopped when its separate rootless image store did not
contain that exact UBI base. The initial diagnostic transferred the pinned image
content from the local Docker store, but a plain `docker save | podman load` can
leave it untagged and is not a sufficient reproducible bootstrap for a
`repository@digest` `FROM` reference.

Use the following Bash commands instead. They require rootless Podman 5.4.2 and
outbound HTTPS access to the public Red Hat registry. The command resolves only
the immutable digest, uses the default TLS verification, and fails if the
stored manifest digest does not match:

```bash
readonly T00_BASE_DIGEST='sha256:194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653'
readonly T00_BASE_IMAGE="registry.access.redhat.com/ubi9/python-314@${T00_BASE_DIGEST}"
podman pull --quiet "${T00_BASE_IMAGE}"
test "$(podman image inspect "${T00_BASE_IMAGE}" --format '{{.Digest}}')" = \
    "${T00_BASE_DIGEST}"
spikes/toolchain/test-validation.sh --runtime podman
```

This idempotently adds or reuses the exact manifest and layers in the invoking
user's rootless Podman store; it does not create a mutable tag or modify the
Docker store. It creates no temporary archive, so there is no partial export to
clean up. Keep the cached base until the validation finishes. This bootstrap is
not registry signature enforcement and does not replace the approved production
supply-chain controls.

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
- Chrome renders Mermaid and reports namespace, PID-namespace,
  network-namespace, and Seccomp-BPF sandbox layers under the Podman profile;
- Podman runtime-default seccomp still fails deterministically at Chrome's
  `sys_chroot` probe, and a modified profile fails its integrity check;
- Fontconfig resolves DejaVu Sans and creates its cache below the arbitrary
  user's writable XDG cache;
- LibreOffice uses an isolated user profile and produces a PDF from the DOCX;
- root execution, non-empty capabilities, missing `no-new-privileges`,
  unconfined seccomp, privileged mode, writable root, host network, wrong
  arbitrary UID, and missing bounded work storage fail deterministically.

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

The final Podman 5.4.2 run used 114,483,200 bytes at the tmpfs probe memory peak
and 114,868,224 bytes at the disk probe peak. It used 904 KiB and 1,232 KiB in
`/work`, respectively. These are compatibility observations for the tiny
fixture, not production budgets.

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

Rootless Podman 5.4.2 reaches a different Chrome failure under its default
seccomp profile after the same security properties pass. Chrome's zygote
terminates before Mermaid rendering with:

```text
Check failed: sys_chroot("/proc/self/fdinfo/") == 0
FATAL:content/browser/zygote_host/zygote_host_impl_linux.cc
```

The installed containers/common 0.62.2 default profile allows `chroot` only
when the container receives `CAP_SYS_CHROOT` and explicitly returns `EPERM`
without it. Chromium's namespace sandbox deliberately chroots a helper into
`/proc/self/fdinfo/` to create an empty filesystem view. The smallest observed
runtime policy change is therefore one syscall rule: retain the complete
runtime-default profile but allow `chroot` without adding a container
capability. [containers/common profile](https://github.com/containers/common/blob/v0.62.2/pkg/seccomp/seccomp.json),
[Chromium sandbox design](https://chromium.googlesource.com/chromium/src/+/main/sandbox/linux/README.md),
[Chromium chroot implementation](https://chromium.googlesource.com/chromium/src/+/main/sandbox/linux/services/credentials.cc)

The committed `spikes/toolchain/chrome-seccomp.json` has SHA-256
`bbd643f78d48b477111dd8597a69ba6bee4db68ce199dbf09d87bf90a1377f46`.
Its upstream Debian profile has SHA-256
`a37993729fdc03beeb0f00c5e31954a1a4412f7624d4672258ac6f5bd44a0ccb`;
the review diff removes only the capability-conditioned `chroot` allow/deny
pair and replaces it with one unconditional seccomp allow rule. This opens the
syscall filter, not the kernel authorization check: the outer container still
reports zero effective and bounding capabilities. The adjacent third-party
notice and Apache-2.0 license preserve the profile's upstream provenance.

With that profile, Mermaid renders and Chrome's `chrome://sandbox` diagnostic
reports layer 1 `Namespace`, PID namespaces `Yes`, network namespaces `Yes`,
Seccomp-BPF `Yes`, TSYNC `Yes`, and “adequately sandboxed.” The container also
reports UID `1000710000`, `CapEff=0`, `CapBnd=0`, `NoNewPrivs=1`, seccomp filter
mode, network `none`, a read-only root, the three bounded writable areas, and
memory/CPU/PID cgroups. No `--disable-setuid-sandbox`, `--no-sandbox`, added
capability, privileged mode, unconfined seccomp, host network, or broad root
write is used in the positive path.

This proves the minimum composition on this rootless Podman host only. k3s must
load and validate the same portable profile before the profile can be approved
for that runtime. OpenShift validation is deferred, and support must not be
claimed until the profile is proven on the target OpenShift security context.

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
LibreOffice path, successful Podman Mermaid rendering and Chrome sandbox-status
diagnostic, and relevant failures: all claimed container security properties,
absent bounded work storage, Pandoc sandbox resource omission in both logs and
OpenXML, Podman runtime-default Chrome rejection, and modified-profile
rejection.

There is no product user-visible or operational workflow in T00, and the final
rootless application image is delivered by T20. Consequently no T00 test can
honestly be an E2E test against that final image. T20/T21 must repeat these
constraints against the final image and real OpenShift/Podman environments. If
this is treated as an E2E exception rather than a non-applicable criterion, it
requires explicit pull-request reviewer approval.

The development VM now has system-installed Podman 5.4.2. The recorded run was
rootless as host UID 1000 on Debian, used `runc` and cgroup v2, and selected
Podman directly through `--runtime podman`. To represent arbitrary container UID
`1000710000` inside the rootless subordinate range, the harness uses sparse UID
and GID mappings. Podman's implicit read-only-root tmpfs mounts are disabled and
replaced by explicit bounded writable mounts for `/tmp`, `/work`, and
`/dev/shm`; all other claimed rootless security properties are asserted inside
the container. Both tmpfs and disk-backed document probes pass. The target
probe additionally renders Mermaid and verifies Chrome's namespace and
Seccomp-BPF layers; every expected failure probe also passes. The Docker 29.7.1
regression suite still treats Chrome's namespace failure as safe negative
evidence. k3s remains unvalidated, and OpenShift remains deferred.

The spike does not read or change a storage contract, so standalone and
distributed storage-profile parity is not affected by T00.

## Remaining validation and configuration decisions

- operational ownership and implementation of the approved official-artifact,
  available-signature, locked-integrity, weekly-review, and urgent-Critical-CVE
  policy;
- k3s validation of the Podman-proven one-syscall seccomp delta; OpenShift proof
  remains deferred;
- exact official font artifacts, versions, notices, substitution order, and
  scripts that explicitly require Noto;
- every production resource, RPO/RTO, retention, quota, antivirus, and cleanup
  value listed in specification section 14;
- PDF/A and table-of-contents support.
