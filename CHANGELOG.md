<a id="changelog"></a>

# Changelog

All notable user-visible, operational, security, compatibility, and deprecation
changes are recorded here. Entries use the release version as their stable link
target. Internal ticket choreography is intentionally excluded.

## Unreleased

<a id="release-0-6-0"></a>

## [0.6.0] - 2026-09-03

### Added

- A separate rootless Next.js frontend image and same-origin production router now serve the
  complete browser workflow while FastAPI remains the sole API and operational authority.
- Release evidence binds the exact tested backend and frontend image bytes, SBOMs, scans,
  provenance, publication receipts, source revision, version, and frontend lockfile.

### Changed

- Browser pages, framework assets, and unknown browser paths route to Next.js; exact API,
  download, OpenAPI, health, readiness, and metrics paths route directly to FastAPI.
- Standalone and distributed deployment examples and quickstarts accept only a matched immutable
  backend/frontend pair. Public defaults remain on the last published pair until the
  post-publication digest-adoption pull request.

### Removed

- The legacy FastAPI-rendered pages and their static browser assets. The backend no longer serves
  `/`, `/login`, `/change-password`, `/convert`, `/templates`, `/logout`, or `/static/**`.

<a id="release-0-5-2"></a>

## [0.5.2] - 2026-09-01

### Changed

- Completed DOCX, PDF, and combined ZIP downloads now preserve the uploaded source filename stem,
  including safe RFC 5987 encoding for non-ASCII names. Retained legacy jobs without source
  metadata continue to use the `conversion-<job-id>` fallback.

<a id="release-0-5-1"></a>

## [0.5.1] - 2026-08-31

### Security

- Markdown conversions now accept strictly validated absolute HTTP(S) hyperlink destinations
  without permitting Pandoc or the document to load remote resources. Remote images and unsafe,
  malformed, credential-bearing, or non-HTTP(S) destinations remain rejected.

<a id="release-0-5-0"></a>

## [0.5.0] - 2026-08-31

### Added

- The installed `markweave` CLI now covers authentication and session profiles,
  conversion and job lifecycles, template administration, user administration,
  audit and health operations, and the local runtime and recovery commands.
- Python installations can select the supported server, standalone, distributed,
  or complete dependency extras without changing the public import surface.

### Changed

- Final source-built containers and their deployment, recovery, smoke, and E2E
  workflows now enter through the supported `markweave` commands.
- The reviewed final-image RPM inventory now includes the UBI `tar` maintenance
  update from `1.34-11.el9` to `1.34-13.el9_8`.
- `MARKWEAVE_*` is now the canonical configuration namespace. During 0.x,
  matching validated `MD_CONVERTER_*` aliases remain compatible; conflicting
  dual definitions fail closed.

### Deprecated

- `MD_CONVERTER_*` configuration aliases are deprecated and will be removed in
  1.0. Migrate to the equivalent `MARKWEAVE_*` settings before that release.

<a id="release-0-4-0"></a>

## [0.4.0] - 2026-08-29

### Added

- Startup CSV user provisioning can create or update local accounts atomically.
- Administrators can require password renewal; affected users receive a restricted
  session until they choose a new password and sign in again.

### Security

- Provisioning updates advance account authentication state and revoke existing
  sessions, preventing stale credentials from retaining access.

<a id="release-0-3-5"></a>

## [0.3.5] - 2026-08-28

### Added

- A loopback-only insecure SSH-tunnel evaluation mode is available for temporary
  testing. It is not a production deployment mode.

<a id="release-0-3-4"></a>

## [0.3.4] - 2026-08-28

### Security

- Quickstart login-origin validation now preserves explicitly configured custom
  loopback ports and same-host reverse-proxy origins.

<a id="release-0-3-3"></a>

## [0.3.3] - 2026-08-27

### Changed

- The trusted-upstream antivirus quickstart uses `slirp4netns` where rootless
  Podman cannot provide the required CNI port mapping.

<a id="release-0-3-2"></a>

## [0.3.2] - 2026-08-27

### Added

- An explicitly configured trusted-upstream malware-scanning boundary is
  supported for the rootless Podman quickstart.

### Security

- The default local ClamAV boundary remains fail closed; trusted upstream mode
  requires an operator-controlled proxy that scans every upload and prevents
  direct application access.

<a id="release-0-3-1"></a>

## [0.3.1] - 2026-08-26

### Fixed

- Release-publication recovery handles a queued publication without changing the
  released package contract.

<a id="release-0-3-0"></a>

## [0.3.0] - 2026-08-25

### Added

- The first approved public Markweave release delivered the browser and HTTP API
  workflow for authenticated Markdown conversion, immutable Word templates,
  asynchronous job status, cancellation, and result download.
- Standalone SQLite/PVC and distributed PostgreSQL/S3-compatible storage
  profiles share the same durable queue and recovery contract.

### Security

- Uploads are scanned by ClamAV before durable processing, unsafe archives and
  remote document resources are rejected, and the final image runs rootlessly
  with a read-only root filesystem and no added capabilities.

### Operations

- Protected automatic package and container publication is bound to an exact
  `main` version transition, verified artifacts, provenance, and release
  evidence.

## Link targets

- [Changelog top](#changelog)
- [0.6.0](#release-0-6-0)
- [0.5.2](#release-0-5-2)
- [0.5.1](#release-0-5-1)
- [0.5.0](#release-0-5-0)
- [0.4.0](#release-0-4-0)
- [0.3.5](#release-0-3-5)
- [0.3.4](#release-0-3-4)
- [0.3.3](#release-0-3-3)
- [0.3.2](#release-0-3-2)
- [0.3.1](#release-0-3-1)
- [0.3.0](#release-0-3-0)
