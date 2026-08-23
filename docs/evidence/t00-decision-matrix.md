# T00 toolchain decision evidence

## Purpose and evidence boundary

This matrix separates reproducible facts from decisions that still require product and security
approval. It does not approve an upstream source, browser sandbox architecture, Markdown dialect,
font set, update cadence, or vulnerability-response owner.

All Web sources are primary publisher or platform sources retrieved on August 23, 2026. Local
observations used Docker 29.7.1 and the committed T00 probe. Podman is not installed on the host.
No k3s test was needed for these local facts, and no OpenShift result is claimed.

## Artifact and supply-chain matrix

| Component | Artifact used by the probe | Integrity, signature, and license evidence | Update and CVE ownership still to approve |
|---|---|---|---|
| UBI 9 / Python 3.14 | `registry.access.redhat.com/ubi9/python-314@sha256:194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653`; reports RHEL 9.8 and Python 3.14.5. The manifest is pinned; RPMs later resolved from public UBI repositories are not. | Red Hat publishes registry signatures and documents verification with its release GPG key and signature server. The harness pins the digest but does not enforce registry signature policy. UBI is freely redistributable under its EULA, source containers are available, and each RPM carries its own license. [Signature verification](https://access.redhat.com/articles/3116561), [UBI catalog](https://catalog.redhat.com/en/software/base-images), [UBI sources](https://access.redhat.com/articles/4238681) | Red Hat owns errata publication. The project must assign digest refresh, RPM snapshot review, scanner triage, and emergency rebuilds. Red Hat targets rebuilds every six weeks or sooner for Important/Critical CVEs; the PM must decide the project's review SLA. [UBI update policy](https://access.redhat.com/support/policy/updates/ubi) |
| Pandoc | Official `pandoc-3.10.2-linux-amd64.tar.gz`; SHA-256 `c7edd535941c48be6a362081a748272837de81ae11777202d9c341d3d8261c9a`. | GitHub release metadata publishes the same digest. Release 3.10.2 has no detached-signature or checksum-list asset, so this path has no independent publisher key. Pandoc is GPL-2.0-or-later with documented exceptions. [Release](https://github.com/jgm/pandoc/releases/tag/3.10.2), [license](https://github.com/jgm/pandoc/blob/3.10.2/COPYRIGHT) | Choose who monitors releases/security notices, validates new digests, reruns the corpus, and triggers urgent rebuilds. A source build or distribution package is an alternative approval path, not selected here. |
| Mermaid CLI | npm `@mermaid-js/mermaid-cli` 11.16.0; the lock fixes the npm graph. Root tarball integrity is `sha512-0InK2nbVIMtzVzCugmdvPkAuvS6wRUqU6Utntff1n8c7lgfRZAdhKY6PSKvcIK9nFmuOUzAgB5+x/XWcroZ7Zg==`. | Registry metadata contains signature key ID `SHA256:DhQ8wR5APBvFHLF/+Tc+AYvPOdTpcIDqOhxsBHRwC7U`, publish attestation, and SLSA provenance linking tag 11.16.0 to commit `c8e5162543e84b18bef3062f7f326821e05dfe2b`. The harness uses lock integrity but does not verify attestations. Root license is MIT; the transitive graph still needs SBOM/license review. [Metadata](https://registry.npmjs.org/@mermaid-js/mermaid-cli/11.16.0), [attestations](https://registry.npmjs.org/-/npm/v1/attestations/@mermaid-js%2fmermaid-cli@11.16.0), [source](https://github.com/mermaid-js/mermaid-cli/tree/11.16.0) | Choose the lock-update owner/cadence, advisory triage, attestation policy, and coordinated browser compatibility test. |
| Google Chrome | Official `google-chrome-stable-151.0.7922.173-1.x86_64.rpm`; SHA-256 `2899353cad3732b8e3a88e76996c340e047d8729ea1b881fdfdd21e0e3baefa5`. | RPM header signature uses subkey `FD533C07C264648F` (fingerprint `0E22 5917 4146 70F4 442C 250D FD53 3C07 C264 648F`) under Google's Linux package key `EB4C 1BFD 4F04 2F6D DDCC EC91 7721 F63B D38B 4796`. The Containerfile checks SHA-256 but does not import/enforce the key. RPM license metadata says `Multiple`; Chrome's distributable-component license inventory needs approval. [Google signing keys](https://www.google.com/linuxrepositories/) | Assign Stable-release/CVE monitoring, signature verification, digest updates, Mermaid regression, and emergency rebuild ownership. Stable normally updates monthly, with additional channel fixes. [Release channels](https://developer.chrome.com/docs/automation-and-testing/release-channels) |
| LibreOffice | Official `LibreOffice_26.2.5_Linux_x86-64_rpm.tar.gz`, producing 26.2.5.2; SHA-256 `f62611c441ff1faa5cadb499abdbab119f5a9013eb6c0e32fc9aa65f6ff8b53d`. | Publisher directory provides `.sha256` and detached `.asc`; the signature identifies fingerprint `C283 9ECA D940 8FBE 9531 C3E9 F434 A1EF AFEE AEA3`. The Containerfile checks SHA-256 but does not verify the signature or establish an approved key channel. LibreOffice's notice states MPL-2.0 and additional third-party terms; installed core RPMs report LGPL. [Artifacts](https://download.documentfoundation.org/libreoffice/stable/26.2.5/rpm/x86_64/), [legal notice](https://api.libreoffice.org/share/readme/LICENSE.html) | Assign release/advisory monitoring, signature verification, subpackage review, corpus regression, and emergency rebuilds. TDF has six-month feature releases and periodic bugfix releases; longer support is provided through ecosystem providers, not assumed for Community binaries. [Release plan](https://wiki.documentfoundation.org/ReleasePlan) |
| UBI fonts / Fontconfig | DejaVu Sans/Mono/Serif 2.37-18.el9, Liberation Mono 2.1.3-5.el9, and Fontconfig 2.14.0-2.el9_1 are in the reviewed 552-RPM inventory. They are snapshots, not immutable RPM pins. | RPMs carry Red Hat RSA/SHA-256 signatures with key ID `199e2f91fd431d51`. Metadata records DejaVu as `Bitstream Vera and Public Domain`, Liberation Mono as `OFL`, and Fontconfig as `MIT and Public Domain and UCD`. [DejaVu license](https://github.com/dejavu-fonts/dejavu-fonts/blob/version_2_37/LICENSE), [Liberation license](https://github.com/liberationfonts/liberation-fonts) | Approve exact families/files, notices, update owner, vulnerability handling, and substitution policy. Font changes need golden-layout review because they can alter pagination. |

The PM/security review can assign these duties to a dedicated maintainer or an explicit rotating
role. Either model must cover publisher-release/advisory discovery, signature/provenance/digest
verification, exact-graph SBOM/license/CVE review, compatibility regression, emergency rebuild,
and rollback. This evidence selects no cadence, SLA, scanner, or owner. The checksum-only Chrome
and LibreOffice steps remain compatibility mechanisms, not an approved production policy.

## Chrome sandbox alternatives

Chromium documents supported user-namespace and setuid layer-1 mechanisms combined with its
seccomp-BPF layer. `--no-sandbox` disables all sandboxing for tests and is not recommended.
[Chromium sandboxing](https://chromium.googlesource.com/chromium/src/+/main/docs/linux/sandboxing.md),
[implementation](https://chromium.googlesource.com/chromium/src/+/main/sandbox/linux/README.md)

| Alternative | Evidence and constraint fit | Status |
|---|---|---|
| Chromium user-namespace sandbox with its seccomp-BPF sandbox | Requires no setuid helper and can in principle retain arbitrary UID, read-only root, dropped capabilities, and `no-new-privileges`. Docker runtime-default seccomp currently denies Chrome's namespace creation. OpenShift supports narrow custom seccomp profiles, but the exact syscall policy and Chrome sandbox status must be reviewed and tested on target CRI-O/OpenShift. [OpenShift custom seccomp](https://docs.redhat.com/en/documentation/openshift_container_platform/4.21/html/security_and_compliance/seccomp-profiles) | Plausible; security/PM approval and real OpenShift proof required. No profile is proposed here. |
| OpenShift pod user namespace (`hostUsers: false`) combined with Chromium's sandbox | OpenShift 4.20 documents `restricted-v3`, which forces a pod user namespace while retaining dropped capabilities, runtime-default seccomp, and no privilege escalation. Kubernetes documents runtime/filesystem prerequisites. It is unproven that Chrome 151 can use the required nested namespace operations under this profile and arbitrary application UID. [OpenShift SCCs](https://docs.redhat.com/en/documentation/openshift_container_platform/4.20/html/authentication_and_authorization/managing-pod-security-policies), [Kubernetes user namespaces](https://kubernetes.io/docs/concepts/workloads/pods/user-namespaces/) | Supported platform primitive, unproven composition; target-cluster proof required. |
| Chromium setuid sandbox | The helper is root-owned mode 4755. `no-new-privileges` prevents privilege gain, and the profile drops capabilities. The probe fails with the expected setuid/namespace errors. | Incompatible with fixed constraints; negative evidence only. |
| Browser in a separately isolated workload/runtime | Could preserve a sandboxed browser while keeping the worker profile strict, but changes workspace transfer, networking, cancellation, and accounting. It must still use a supported Chrome sandbox. | Architectural fallback only; PM decision and separate threat model required. |

T09 must not convert the Docker failure into permission to weaken Chrome. A selected supported
composition needs successful Mermaid rendering plus sandbox-status, network, capability, UID,
read-only-root, writable-area, and resource-limit evidence on real OpenShift.

## Pandoc 3.10.2 CommonMark compatibility

The probe converts `fixtures/commonmark-compatibility.md` to Pandoc JSON and verifies structure:

| Reader expression | Observed result |
|---|---|
| `commonmark` | Parses headings, lists, quotes, fenced code, links, and images; does not produce `Table` or `Note` nodes for the extension fixtures. |
| `commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html` | Produces baseline structures plus `Table`, `Note`, YAML title metadata, and image ID/width attributes. Despite `-raw_html`, the HTML fixture still produces a raw HTML AST node, so this flag is not an effective security control in 3.10.2. |
| `commonmark_x-yaml_metadata_block` | Leaves metadata empty, proving that metadata is independently controllable. |
| `commonmark_x-raw_tex` | Fails because `raw_tex` is unsupported for `commonmark_x`. The TeX-like fixture is ordinary text under this reader; pre-parse rejection is still needed if the input contract forbids such constructs categorically. |

Pandoc documents its extension model and sandbox semantics in the [official manual](https://pandoc.org/MANUAL.html).
No final dialect is selected. The PM must approve the reader expression and whether raw HTML is
rejected before Pandoc, removed from the AST, or handled by another tested policy.

## Font inventory and substitution candidates

| Candidate | Evidence | Approval gap |
|---|---|---|
| DejaVu Sans, Serif, Mono | Present from public UBI repositories. Broad Latin/Greek/Cyrillic mechanics are proven; Microsoft Office metric compatibility is not claimed. Upstream uses a Bitstream Vera-derived license plus public-domain additions. [License](https://github.com/dejavu-fonts/dejavu-fonts/blob/version_2_37/LICENSE) | Decide allowed scripts/fallback roles and acceptable reflow. |
| Liberation Sans, Serif, Mono | Only Mono is available to the current public UBI query/build. Upstream OFL-1.1 project targets metric compatibility with Arial, Times New Roman, and Courier New. [Upstream](https://github.com/liberationfonts/liberation-fonts) | Approve source for Sans/Serif, exact families/variants, glyph coverage, and golden pagination. |
| Carlito and Caladea | Not available from the public UBI query. Carlito is OFL and metric-compatible with Calibri; LibreOffice identifies Carlito/Caladea as metric candidates for Calibri/Cambria. [Carlito](https://github.com/googlefonts/carlito), [LibreOffice notes](https://wiki.documentfoundation.org/ReleaseNotes/4.4#Included_fonts) | Approve external source, releases/digests, notices, variants, and rendering tolerance. |
| Noto families | Not available from the public UBI query. OFL families are candidates for explicit multilingual coverage. [Noto licensing](https://notofonts.github.io/noto-docs/website/use/) | Approve required scripts/subsets, fallback order, emoji policy, exact artifacts, and image-size impact. |

T10 should enforce declared required fonts and only permit an approved substitution map. This
inventory approves neither the rule nor the mapping.

## Local runtime evidence and remaining gates

- Docker reran the engine/security probe with bounded tmpfs `/work` and with a dedicated
  disk-backed bind mount. The disk run retained arbitrary UID `1000710000`, read-only root, no
  network, all capabilities dropped, `no-new-privileges`, and memory/CPU/PID cgroups, and completed
  Pandoc, Fontconfig, and LibreOffice. It used 1,232 KiB and approximately 112 MB peak memory for
  the tiny fixture; these are not budgets.
- The bind proves disk compatibility, not a bounded disk volume. Final proof needs an approved
  `emptyDir.sizeLimit`/ephemeral-storage configuration or equivalent on the target runtime.
- Podman is absent. OpenShift remains PM-deferred. k3s was unnecessary and cannot establish
  OpenShift SCC/CRI-O behavior.
- Source approvals, ownership/cadence, exact Markdown dialect, Chrome sandbox, fonts/substitution,
  production limits, and retention remain unresolved.
