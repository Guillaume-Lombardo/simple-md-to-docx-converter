# T00 document toolchain validation

## Status

The document engines are compatible with UBI 9 and Python 3.14 in a reproducible
laboratory image. The intended OpenShift Chrome sandbox profile is **not
validated**. Do not use this spike as the production image and do not enable
Pandoc `--sandbox` for conversions with local resources.

This report records evidence observed on August 23, 2026. Artifact versions are
pinned only for repeatability of the spike; they are not product upgrade-policy
decisions.

## Reproduce the evidence

Requirements:

- Docker with BuildKit and permission to run containers;
- outbound access during the image build to the pinned upstream URLs;
- a Linux host with cgroup v2 and support for container seccomp options.

Run the complete successful laboratory probe:

```bash
spikes/toolchain/run-validation.sh namespace-lab
```

Run the successful probe and all expected-failure probes:

```bash
spikes/toolchain/test-validation.sh
```

The test script verifies:

- Python 3.14 runs as an arbitrary non-root UID;
- the root filesystem is read-only and only bounded `/tmp` and `/work` tmpfs
  mounts are writable;
- the container has no network and no Linux capabilities, and has
  `no-new-privileges` enabled;
- Pandoc produces a valid DOCX with an embedded local PNG and a reference DOCX;
- Pandoc `--sandbox` cannot resolve that local resource and reports the failure;
- Mermaid CLI and Chrome render a local diagram in the namespace-sandbox lab
  profile;
- Fontconfig resolves DejaVu Sans and creates its cache below the arbitrary
  user's writable XDG cache;
- LibreOffice uses an isolated user profile and produces a PDF from the DOCX;
- root execution, missing writable work storage, and the intended Chrome target
  security profile fail deterministically.

`run-validation.sh` accepts laboratory-only resource controls through
`TOOLCHAIN_MEMORY`, `TOOLCHAIN_CPUS`, `TOOLCHAIN_PIDS`,
`TOOLCHAIN_WORK_SIZE`, and `TOOLCHAIN_TMP_SIZE`. Their defaults (2 GiB, 2 CPUs,
512 processes, 1 GiB, and 512 MiB) are probe envelopes, not product defaults.
The probe prints cgroup memory/process peaks and final `/work` usage so T18 can
use measured corpus results when it defines the real budgets.

The tiny fixture used 227,397,632 bytes at the cgroup memory peak, 118 processes
at the cgroup PID peak, and 988 KiB in `/work` during the recorded successful
run. These values describe this one probe only.

## Pinned artifact inventory

| Component | Pinned evidence | Source status |
|---|---|---|
| Base image | `registry.access.redhat.com/ubi9/python-314@sha256:194df4e35e0e5467e1b57266f4d61f821e1b1f567135f074d23066d3604ae653` | Red Hat UBI public registry |
| Base runtime | RHEL 9.8, Python 3.14.5, Node.js 22.23.1 | Included by the pinned image |
| Pandoc | 3.10.2, SHA-256 `c7edd535941c48be6a362081a748272837de81ae11777202d9c341d3d8261c9a` | Official GitHub release; production approval pending |
| Mermaid CLI | 11.16.0, full npm dependency lock | Official npm package; production approval pending |
| Chrome | 151.0.7922.173-1, SHA-256 `2899353cad3732b8e3a88e76996c340e047d8729ea1b881fdfdd21e0e3baefa5` | Official Google RPM; production approval and signature policy pending |
| LibreOffice | 26.2.5.2, archive SHA-256 `f62611c441ff1faa5cadb499abdbab119f5a9013eb6c0e32fc9aa65f6ff8b53d` | Official Document Foundation RPM archive; production approval pending |
| Fontconfig | 2.14.0-2.el9_1 | UBI AppStream, MIT/Public Domain/UCD RPM metadata |
| DejaVu | 2.37-18.el9 | UBI BaseOS, Bitstream Vera/Public Domain RPM metadata |
| Liberation Mono | 2.1.3-5.el9 | UBI AppStream, OFL RPM metadata |

The public UBI BaseOS, AppStream, and CodeReady Builder repositories exposed by
the pinned base image contain Fontconfig and the selected probe fonts. Queries
for `pandoc`, `chromium`, and `libreoffice` returned no packages. The upstream
artifacts above therefore prove technical compatibility only. They do not settle
which non-UBI sources, keys, licenses, update cadence, or vulnerability response
process are approved for the production build.

The resulting laboratory image was 1,119,334,954 bytes. T20 must select only the
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
unavailable. The exact Markdown extension list remains unresolved as required by
section 14 of the product specification.

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
The laboratory control succeeds with `--disable-setuid-sandbox` plus
`seccomp=unconfined`; it keeps Chrome's namespace sandbox path but is not an
acceptable OpenShift restricted-profile result. The spike never uses
`--no-sandbox` in its committed workflow.

A product/security decision is required before T09:

1. design, approve, and validate a narrow custom seccomp profile that enables
   the namespace sandbox on a real OpenShift restricted workload; or
2. select a browser/runtime architecture whose supported sandbox works with the
   fixed OpenShift security context.

Accepting `--no-sandbox` would weaken the stated security boundary and is not
recommended. No option is silently selected by T00.

### Fonts and document conversion

The laboratory conversion resolves DejaVu Sans deterministically, creates a
per-user Fontconfig cache on writable storage, embeds the local image in DOCX,
and converts that DOCX to a valid PDF with an isolated LibreOffice user profile.
This establishes mechanics only. T10 must still decide the approved fonts,
licenses, expected-template font declarations, substitution policy, required
styles, and golden rendering corpus.

### Resource budgets

The harness proves that Docker enforces configurable memory, CPU, PID, `/tmp`,
and `/work` envelopes and exposes peak measurements. A single tiny fixture is
not evidence for production limits. Upload, archive, image, diagram, duration,
memory, concurrency, queue, and retention values remain unresolved for T18 and
must be measured with the T04 corpus rather than copied from the spike defaults.

## Coverage and remaining validation

The spike crosses real subprocess, filesystem, font, browser, and container
boundaries. `test-validation.sh` covers the successful laboratory path and the
relevant failures discovered in T00: root execution, absent writable storage,
Pandoc sandbox resource omission, and Chrome sandbox rejection under the target
profile.

There is no product user-visible or operational workflow in T00, and the final
rootless application image is delivered by T20. Consequently no T00 test can
honestly be an E2E test against that final image. T20/T21 must repeat these
constraints against the final image and real OpenShift/Podman environments. If
this is treated as an E2E exception rather than a non-applicable criterion, it
requires explicit pull-request reviewer approval.

Podman and a real OpenShift cluster were unavailable in the local environment;
Docker 29.7.1 was used for the container evidence. The Python project,
`pyproject.toml`, lockfile, Ruff, `ty`, and Pytest setup are owned by T01/T05 and
do not exist on the T00 base revision. Canonical Python checks therefore have a
documented bootstrap gap rather than a passing result.

The spike does not read or change a storage contract, so standalone and
distributed storage-profile parity is not affected by T00.

## Decisions deliberately left unresolved

- approved upstream sources, signing keys, licenses, update ownership, and
  vulnerability response for Pandoc, Chrome/Chromium, Mermaid, and LibreOffice;
- the Chrome/OpenShift sandbox architecture and seccomp policy;
- approved fonts and substitution rules;
- exact Markdown extensions;
- every production resource and retention limit listed in specification
  section 14;
- PDF/A and table-of-contents support.
