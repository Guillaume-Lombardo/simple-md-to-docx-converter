# T00 toolchain decision evidence

## Purpose and evidence boundary

This matrix separates reproducible facts from the product decisions approved on August 23, 2026.
It records the approved artifact policy, browser-sandbox direction, Markdown dialect, font set, and
vulnerability cadence without turning unexecuted k3s or OpenShift work into evidence.

All Web sources are primary publisher or platform sources retrieved on August 23, 2026. Local
observations used Docker 29.7.1, rootless Podman 5.4.2, and the committed T00 probe. Podman ran as
host UID 1000 on Debian with `runc` and cgroup v2. No k3s or OpenShift result is claimed.

## Artifact and supply-chain matrix

| Component | Artifact used by the probe | Integrity, signature, and license evidence | Approved production policy |
|---|---|---|---|
| UBI 9 / Python 3.14 | `registry.access.redhat.com/ubi9/python-314@sha256:194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653`; reports RHEL 9.8 and Python 3.14.5. The manifest is pinned; RPMs later resolved from public UBI repositories are not. | Red Hat publishes registry signatures and documents verification with its release GPG key and signature server. The harness pins the digest but does not enforce registry signature policy. UBI is freely redistributable under its EULA, source containers are available, and each RPM carries its own license. [Signature verification](https://access.redhat.com/articles/3116561), [UBI catalog](https://catalog.redhat.com/en/software/base-images), [UBI sources](https://access.redhat.com/articles/4238681) | Use official publisher artifacts, verify the registry signature, pin the manifest digest, review the resolved RPM inventory weekly, and handle Critical findings urgently. [UBI update policy](https://access.redhat.com/support/policy/updates/ubi) |
| Pandoc | Official `pandoc-3.10.2-linux-amd64.tar.gz`; SHA-256 `c7edd535941c48be6a362081a748272837de81ae11777202d9c341d3d8261c9a`. | GitHub release metadata publishes the same digest. Release 3.10.2 has no detached-signature or checksum-list asset, so this path has no independent publisher key. Pandoc is GPL-2.0-or-later with documented exceptions. [Release](https://github.com/jgm/pandoc/releases/tag/3.10.2), [license](https://github.com/jgm/pandoc/blob/3.10.2/COPYRIGHT) | The official release SHA-256 is accepted when no detached signature exists. Lock the digest, review weekly, regress the corpus on update, and handle Critical findings urgently. |
| Mermaid CLI | npm `@mermaid-js/mermaid-cli` 11.16.0; the lock fixes the npm graph. Root tarball integrity is `sha512-0InK2nbVIMtzVzCugmdvPkAuvS6wRUqU6Utntff1n8c7lgfRZAdhKY6PSKvcIK9nFmuOUzAgB5+x/XWcroZ7Zg==`. | Registry metadata contains signature key ID `SHA256:DhQ8wR5APBvFHLF/+Tc+AYvPOdTpcIDqOhxsBHRwC7U`, publish attestation, and SLSA provenance linking tag 11.16.0 to commit `c8e5162543e84b18bef3062f7f326821e05dfe2b`. The harness uses lock integrity but does not verify attestations. Root license is MIT; the transitive graph still needs SBOM/license review. [Metadata](https://registry.npmjs.org/@mermaid-js/mermaid-cli/11.16.0), [attestations](https://registry.npmjs.org/-/npm/v1/attestations/@mermaid-js%2fmermaid-cli@11.16.0), [source](https://github.com/mermaid-js/mermaid-cli/tree/11.16.0) | Use the official package, lock the full graph, verify available attestations/provenance, review the SBOM weekly, and handle Critical findings urgently. |
| Google Chrome | Official `google-chrome-stable-151.0.7922.173-1.x86_64.rpm`; SHA-256 `2899353cad3732b8e3a88e76996c340e047d8729ea1b881fdfdd21e0e3baefa5`. | RPM header signature uses subkey `FD533C07C264648F` (fingerprint `0E22 5917 4146 70F4 442C 250D FD53 3C07 C264 648F`) under Google's Linux package key `EB4C 1BFD 4F04 2F6D DDCC EC91 7721 F63B D38B 4796`. The Containerfile checks SHA-256 but does not import/enforce the key. RPM license metadata says `Multiple`; Chrome's distributable-component license inventory needs approval. [Google signing keys](https://www.google.com/linuxrepositories/) | Use the official RPM, verify its signature, lock its SHA-256, review weekly, rerun Mermaid/sandbox regression on update, and handle Critical findings urgently. [Release channels](https://developer.chrome.com/docs/automation-and-testing/release-channels) |
| LibreOffice | Official `LibreOffice_26.2.5_Linux_x86-64_rpm.tar.gz`, producing 26.2.5.2; SHA-256 `f62611c441ff1faa5cadb499abdbab119f5a9013eb6c0e32fc9aa65f6ff8b53d`. | Publisher directory provides `.sha256` and detached `.asc`; the signature identifies fingerprint `C283 9ECA D940 8FBE 9531 C3E9 F434 A1EF AFEE AEA3`. The Containerfile checks SHA-256 but does not verify the signature or establish an approved key channel. LibreOffice's notice states MPL-2.0 and additional third-party terms; installed core RPMs report LGPL. [Artifacts](https://download.documentfoundation.org/libreoffice/stable/26.2.5/rpm/x86_64/), [legal notice](https://api.libreoffice.org/share/readme/LICENSE.html) | Use the official archive, verify its detached signature and SHA-256, review weekly, regress the corpus on update, and handle Critical findings urgently. [Release plan](https://wiki.documentfoundation.org/ReleasePlan) |
| UBI fonts / Fontconfig | DejaVu Sans/Mono/Serif 2.37-18.el9, Liberation Mono 2.1.3-5.el9, and Fontconfig 2.14.0-2.el9_1 are in the reviewed 552-RPM inventory. They are snapshots, not immutable RPM pins. | RPMs carry Red Hat RSA/SHA-256 signatures with key ID `199e2f91fd431d51`. Metadata records DejaVu as `Bitstream Vera and Public Domain`, Liberation Mono as `OFL`, and Fontconfig as `MIT and Public Domain and UCD`. [DejaVu license](https://github.com/dejavu-fonts/dejavu-fonts/blob/version_2_37/LICENSE), [Liberation license](https://github.com/liberationfonts/liberation-fonts) | Use official artifacts for the approved Liberation, Carlito/Caladea, DejaVu-fallback, and explicitly required Noto set. Verify available signatures, lock integrity, review weekly, and use golden-layout review for every update. Exact artifacts and substitution details remain T10 work. |

The PM approved official publisher artifacts, available signature/provenance verification, locked
digests/checksums, weekly vulnerability review, and urgent handling of Critical vulnerabilities.
The operational owner and scanner remain implementation choices, but they must cover release and
advisory discovery, exact-graph SBOM/license/CVE review, compatibility regression, emergency
rebuild, and rollback. The current checksum-only Chrome and LibreOffice probe remains compatibility
evidence; production implementation must add the approved signature checks.

## Chrome sandbox alternatives

Chromium documents supported user-namespace and setuid layer-1 mechanisms combined with its
seccomp-BPF layer. `--no-sandbox` disables all sandboxing for tests and is not recommended.
[Chromium sandboxing](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/sandboxing.md),
[implementation](https://chromium.googlesource.com/chromium/src/+/main/sandbox/linux/README.md)

| Alternative | Evidence and constraint fit | Status |
|---|---|---|
| Chromium user-namespace sandbox with its seccomp-BPF sandbox | Requires no setuid helper and can in principle retain arbitrary UID, read-only root, dropped capabilities, and `no-new-privileges`. Docker runtime-default seccomp currently denies Chrome's namespace creation. OpenShift supports narrow custom seccomp profiles, but the exact syscall policy and Chrome sandbox status must be reviewed and tested on target CRI-O/OpenShift. [OpenShift custom seccomp](https://docs.redhat.com/en/documentation/openshift_container_platform/4.21/html/security_and_compliance/seccomp-profiles) | Approved direction: develop the minimum seccomp/user-namespace profile and validate it on rootless Podman, then k3s. OpenShift proof is deferred. |
| OpenShift pod user namespace (`hostUsers: false`) combined with Chromium's sandbox | OpenShift 4.20 documents `restricted-v3`, which forces a pod user namespace while retaining dropped capabilities, runtime-default seccomp, and no privilege escalation. Kubernetes documents runtime/filesystem prerequisites. It is unproven that Chrome 151 can use the required nested namespace operations under this profile and arbitrary application UID. [OpenShift SCCs](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/authentication_and_authorization/managing-pod-security-policies), [Kubernetes user namespaces](https://kubernetes.io/docs/concepts/workloads/pods/user-namespaces/) | Supported platform primitive, unproven composition; target-cluster proof required. |
| Chromium setuid sandbox | The helper is root-owned mode 4755. `no-new-privileges` prevents privilege gain, and the profile drops capabilities. The probe fails with the expected setuid/namespace errors. | Incompatible with fixed constraints; negative evidence only. |
| Browser in a separately isolated workload/runtime | Could preserve a sandboxed browser while keeping the worker profile strict, but changes workspace transfer, networking, cancellation, and accounting. It must still use a supported Chrome sandbox. | Architectural fallback only; PM decision and separate threat model required. |

T09 must not convert the Docker failure into permission to weaken Chrome and must never use
`--no-sandbox`. The supported composition needs successful Mermaid rendering plus sandbox-status,
network, capability, UID, read-only-root, writable-area, and resource-limit evidence on rootless
Podman and k3s. Real OpenShift evidence remains deferred and is required before claiming support.

The current rootless Podman harness maps container UID `1000710000` sparsely into the subordinate
UID range and exposes only explicit bounded writable mounts. These runtime mechanics pass, but
Chrome still terminates in its zygote at `sys_chroot("/proc/self/fdinfo/")` before Mermaid renders.
Docker continues to fail earlier with namespace creation denied by `EPERM`. Neither result proves
the approved minimal seccomp/user-namespace composition; both are safe negative evidence gathered
without `--no-sandbox`, `seccomp=unconfined`, added capabilities, privileged mode, or network
access in the target probe.

## Pandoc 3.10.2 CommonMark compatibility

The probe converts `fixtures/commonmark-compatibility.md` to Pandoc JSON and verifies structure:

| Reader expression | Observed result |
|---|---|
| `commonmark` | Parses headings, lists, quotes, fenced code, links, and images; does not produce `Table` or `Note` nodes for the extension fixtures. |
| `commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html` | Produces baseline structures plus `Table`, `Note`, YAML title metadata, and image ID/width attributes. Despite `-raw_html`, the HTML fixture still produces a raw HTML AST node, so this flag is not an effective security control in 3.10.2. |
| `commonmark_x-yaml_metadata_block` | Leaves metadata empty, proving that metadata is independently controllable. |
| `commonmark_x-raw_tex` | Fails because `raw_tex` is unsupported for `commonmark_x`. The TeX-like fixture is ordinary text under this reader; pre-parse rejection is still needed if the input contract forbids such constructs categorically. |

Pandoc documents its extension model and sandbox semantics in the [official manual](https://pandoc.org/MANUAL.html).
The PM approved
`commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html` and mandatory raw-HTML
rejection before Pandoc. The reader flag alone is not treated as a security boundary.

## Font inventory and substitution candidates

| Candidate | Evidence | Approval gap |
|---|---|---|
| DejaVu Sans, Serif, Mono | Present from public UBI repositories. Broad Latin/Greek/Cyrillic mechanics are proven; Microsoft Office metric compatibility is not claimed. Upstream uses a Bitstream Vera-derived license plus public-domain additions. [License](https://github.com/dejavu-fonts/dejavu-fonts/blob/version_2_37/LICENSE) | Decide allowed scripts/fallback roles and acceptable reflow. |
| Liberation Sans, Serif, Mono | Only Mono is available to the current public UBI query/build. Upstream OFL-1.1 project targets metric compatibility with Arial, Times New Roman, and Courier New. [Upstream](https://github.com/liberationfonts/liberation-fonts) | Approve source for Sans/Serif, exact families/variants, glyph coverage, and golden pagination. |
| Carlito and Caladea | Not available from the public UBI query. Carlito is OFL and metric-compatible with Calibri; LibreOffice identifies Carlito/Caladea as metric candidates for Calibri/Cambria. [Carlito](https://github.com/googlefonts/carlito), [LibreOffice notes](https://wiki.documentfoundation.org/ReleaseNotes/4.4#Included_fonts) | Approve external source, releases/digests, notices, variants, and rendering tolerance. |
| Noto families | Not available from the public UBI query. OFL families are candidates for explicit multilingual coverage. [Noto licensing](https://notofonts.github.io/noto-docs/website/use/) | Approve required scripts/subsets, fallback order, emoji policy, exact artifacts, and image-size impact. |

The PM approved Liberation plus Carlito/Caladea, DejaVu as fallback, and Noto only for scripts
explicitly required by the approved corpus or template contract. T10 must pin official artifacts,
validate licenses and notices, enforce declared required fonts, and codify the exact substitution
order with golden-layout evidence.

## Local runtime evidence and remaining gates

- Docker reran the engine/security probe with bounded tmpfs `/work` and with a dedicated
  disk-backed bind mount. The disk run retained arbitrary UID `1000710000`, read-only root, no
  network, all capabilities dropped, `no-new-privileges`, and memory/CPU/PID cgroups, and completed
  Pandoc, Fontconfig, and LibreOffice. It used 1,232 KiB and approximately 112 MB peak memory for
  the tiny fixture; these are not budgets.
- The bind proves disk compatibility, not a bounded disk volume. Final proof needs an approved
  `emptyDir.sizeLimit`/ephemeral-storage configuration or equivalent on the target runtime.
- System-installed Podman 5.4.2 ran rootless as host UID 1000 on Debian with `runc` and cgroup v2.
  The exact runtime selector preserves Docker as the default and invokes Podman without aliases.
  Sparse UID/GID mappings represent container UID `1000710000`; explicit bounded `/tmp`, `/work`,
  and `/dev/shm` mounts preserve arbitrary-UID write access while the root remains read-only.
- Podman's tmpfs and disk-backed document probes complete Pandoc, Fontconfig, and LibreOffice, and
  all expected security failure probes pass. The Docker regression suite also passes. Podman
  records approximately 114.5 MB and 114.9 MB cgroup memory peaks and 904 KiB and 1,232 KiB
  `/work` use for the tiny tmpfs and disk fixtures; these are not budgets.
- The first Podman build stopped because `--pull=false` found no pinned UBI base in Podman's
  separate store. The exact digest was preloaded from the local Docker store before the unchanged
  harness passed. This is a reproducibility caveat, not production registry-signature proof.
- Chrome still fails before Mermaid rendering: Podman reports the zygote `sys_chroot` fatal, while
  Docker retains its namespace `EPERM` failure. The minimal sandbox profile, k3s validation, and
  deferred OpenShift proof remain outstanding.
- Production limits, RPO/RTO, retention, quotas, antivirus, cleanup, exact font artifacts and
  substitutions, and explicit Noto script coverage remain configurable or unresolved.
