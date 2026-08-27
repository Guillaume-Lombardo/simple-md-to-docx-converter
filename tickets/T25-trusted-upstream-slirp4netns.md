---
ticket: T25
linear_id: G1L-335
linear_url: https://linear.app/g1lom/issue/G1L-335/
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T25 - Support slirp4netns trusted-upstream quickstart

## Objective

Allow the rootless Podman simple quickstart to run in explicit trusted-upstream antivirus mode on
hosts where CNI port mapping cannot use nftables, publish the correction as patch release `0.3.3`,
and preserve the isolated ClamAV topology in the default mode.

## Acceptance criteria

- Rootless Podman trusted-upstream mode uses `slirp4netns` without creating CNI bridge networks or
  invoking the CNI `portmap` plugin.
- The workaround applies only when both Podman and `--trust-upstream-antivirus` are selected;
  Docker and default ClamAV behavior remain unchanged.
- Startup fails early with a clear error if `slirp4netns` is unavailable for this workaround.
- Compose-contract and quickstart tests verify overlay selection, rendered network mode, published
  localhost port, and unchanged default topology.
- User documentation explains the compatibility path and trusted-upstream security boundary.
- The quickstart is repinned to the verified immutable `0.3.3` image after its protected automatic
  release succeeds.
- Project, application, lock, README, and release-test version surfaces move from `0.3.2` to
  `0.3.3`.
- Relevant canonical checks pass, with unavailable external-engine or service validation reported
  explicitly.

## Dependencies

- T24

## Progress

- 2026-08-27: Started after rootless Podman 4.9.4 on an LXD-hosted RHEL 8 environment selected CNI
  while `iptables-nft` failed with `Protocol not supported`; Netavark and Aardvark DNS were
  unavailable, but `slirp4netns` was installed.
- 2026-08-27: Added the trusted-upstream-only Podman overlay, early `slirp4netns` prerequisite,
  immutable `0.3.2` image repin, operator documentation, and the complete `0.3.3` version
  transition. Docker Compose renders one Markweave service with `network_mode=slirp4netns`, no
  Compose networks, trusted-upstream scanning mode, and the loopback-only published port; the
  default Podman model retains the scanner, frontend, and signature-update networks.
- 2026-08-27: Ruff format/check, `ty`, ShellCheck, Bash syntax, lock validation, all 23 Web tests,
  21 focused quickstart/version tests, 33 release and install tests, and all 1,338 unit tests pass
  at 93.64% coverage. A rootless Podman probe successfully served HTTP through `slirp4netns` at
  `127.0.0.1` and reported the expected port binding. The complete Markweave quickstart lifecycle
  could not finish because `/var/tmp` had only 185 MiB free and the `0.3.2` image pull failed with
  `no space left on device`; PostgreSQL/RustFS settings and document engines are also unavailable
  for the remaining canonical suites.
- 2026-08-27: Pull request #91 passed all 12 ready-PR jobs and was squash-merged to `main` at
  `adaf16ed62eb88b50c6e5b45b0c612a36b898d7b`. The post-merge full-domain CI matrix also passed.
  Automatic release run 33106024542 published final GitHub Release and tag `v0.3.3`, the PyPI wheel
  and source distribution, and the attested GHCR image with SBOM and release evidence. The
  quickstart now pins that image at registry digest
  `sha256:dbd23aea3daee03255b803add829a087c0490b00db9bd342996fc5051b652ffc`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
or progress changes.
