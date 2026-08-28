---
ticket: T26
linear_id: G1L-384
linear_url: https://linear.app/g1lom/issue/G1L-384/
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T26 - Preserve login origin on custom quickstart ports

## Objective

Make the simple and secure Compose quickstarts configure Markweave's public origin from the
selected loopback port so browser login remains same-origin when `MARKWEAVE_SIMPLE_PORT` or
`MARKWEAVE_QUICKSTART_PORT` differs from 8080, while allowing an exact reverse-proxy origin.

## Acceptance criteria

- The simple quickstart passes an exact loopback public origin using its selected port to the
  application.
- The secure quickstart passes the same exact loopback public origin contract.
- Operators can explicitly configure the exact browser-visible origin for a same-host reverse
  proxy without trusting forwarded-host headers.
- The default port remains 8080 and existing runtime and network behavior is unchanged.
- Tests verify the committed configuration and rendered Compose configuration for a non-default
  port.
- User documentation states that changing the quickstart port preserves browser login origin
  validation.
- Project, application, lock, README, and release-test version surfaces move from `0.3.3` to
  `0.3.4`; Compose remains pinned to the last published immutable image until the protected release
  succeeds.
- Relevant canonical checks pass, with unavailable external-engine or service validation reported
  explicitly.

## Dependencies

- T25

## Progress

- 2026-08-28: Started after reproducing that a simple Podman trusted-upstream quickstart published
  on port 11279 rejects browser login with `LOGIN_ORIGIN_INVALID` when its external origin and the
  application-visible internal origin differ.
- 2026-08-28: Added a default `http://localhost:<selected-port>` public origin to both quickstart
  helpers, an explicit `MARKWEAVE_PUBLIC_ORIGIN` override for same-host HTTPS reverse proxies,
  single-line environment-file validation, Compose rendering coverage, operator documentation,
  and the complete `0.3.4` version transition. Compose intentionally remains pinned to the
  published immutable `0.3.3` image until the protected `0.3.4` release succeeds.
- 2026-08-28: Ruff format/check, `ty`, ShellCheck, Bash syntax, lock validation, all 23 Web tests,
  and 36 focused quickstart, Compose, version, and release tests pass. The canonical engine-excluded
  suite reached 1,538 passing tests and 95.09% coverage but could not complete because PostgreSQL
  and RustFS configuration/services are unavailable (26 setup errors and 3 S3 failures). The full
  external-engine suite was not run because those required services are unavailable.
- 2026-08-28: Pull request #93 passed its complete ready-PR matrix after rerunning three transient
  LibreOffice download failures and was squash-merged to `main` at
  `8ac413b77807c9ed2431924527a396619ea2d93e`. Automatic release run 33159815107 published final
  GitHub Release and tag `v0.3.4`, the PyPI wheel and source distribution, and the attested GHCR
  image with SBOM and release evidence. The quickstart now pins that anonymously readable image at
  registry digest `sha256:2e525afc5c080326b712bf05fbe875879b2b8ea835692556fec51b7c875899f8`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
or progress changes.
