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
