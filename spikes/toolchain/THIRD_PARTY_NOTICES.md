# Third-party notices for the T00 toolchain spike

## containers/common seccomp profile

`chrome-seccomp.json` is derived from
[`pkg/seccomp/seccomp.json`](https://github.com/containers/common/blob/v0.62.2/pkg/seccomp/seccomp.json)
in containers/common v0.62.2. containers/common is licensed under the Apache
License 2.0; the license text is included in `LICENSE.containers-common`.

The local modification replaces only the two capability-conditioned `chroot`
rules with one unconditional seccomp allow rule. The remaining profile content
is unchanged from the upstream v0.62.2 file installed by Debian package
`golang-github-containers-common` 0.62.2+ds1-2.

## Document fonts

The image contains only the 32 faces listed in `fonts/manifest.json`:

- Liberation 2.1.5 under the SIL Open Font License 1.1, with the upstream
  `LICENSE` installed as `Liberation-LICENSE`;
- Carlito 1.104 and Caladea 1.001 under the SIL Open Font License 1.1, with
  their upstream `OFL.txt` files installed separately;
- DejaVu 2.37 under the complete Bitstream Vera, DejaVu public-domain, and Arev
  terms installed as `DejaVu-LICENSE`.

The current approved corpus requires Latin and Greek, both covered by this set.
No Noto family is installed. A future script expansion requires a separately
reviewed, versioned, and checksum-locked image-manifest change.
