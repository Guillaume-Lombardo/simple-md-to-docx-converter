<a id="changelog"></a>

# Changelog

All notable user-visible, operational, security, compatibility, and deprecation
changes are recorded here. Entries use the release version as their stable link
target. Internal ticket choreography is intentionally excluded.

Use the [upgrade guide](docs/upgrading.md) for supported transitions and rollback, the
[release process](docs/releasing.md) for artifact evidence, the [security policy](SECURITY.md) for
private reporting, and the [support policy](SUPPORT.md) for help with a published release.

<a id="release-0-7-5"></a>

## [0.7.5] - 2026-10-12

### Security

- The Next.js frontend moves from `16.3.4` to `16.3.8`, fixing the Critical advisory
  GHSA-vcvr-r3jv-pc5j and the High advisory GHSA-cjq9-62q9-8jv4 together with lower-severity
  Next.js advisories. `eslint-config-next` follows the same patch.
- The frontend lockfile adopts fixed releases of `sharp` `0.35.5`, `undici` `7.30.0`,
  `brace-expansion` `1.1.21` and `5.0.12`, `js-yaml` `4.3.2` and `source-map-js` `1.2.2`. No High
  or Critical advisory with an available fix remains in the frontend dependency tree.

### Release notes

- Version 0.7.4 was published to PyPI only. Its container release stopped at the
  Critical-vulnerability gate before any image was published, so no `0.7.4` backend, frontend or
  reverse-attempt image exists. Version 0.7.5 contains every 0.7.4 change and supersedes it for
  container deployments: upgrade containers from `0.7.3` directly to `0.7.5`. Never invent
  `0.7.4` image digests or run a `0.7.4` or `0.7.5` package against `0.7.3` images.
- Repository Compose and quickstart defaults remain pinned to the verified `0.7.3` backend and
  frontend digests until the `0.7.5` images are published and their exact receipts are adopted.

<a id="release-0-7-4"></a>

## [0.7.4] - 2026-10-11

### Added

- `markweave convert SOURCE --local` converts a local file without a server, an account or ClamAV.
  It runs the service's validation, archive, image, Mermaid, Pandoc and LibreOffice adapters with
  the same `MARKWEAVE_*` limits, records the engine versions it used in a content-free
  traceability manifest, fails clearly when a required engine is missing, and warns when an engine
  version differs from the approved pin; `--require-pinned` turns that warning into a failure. A
  documented container invocation runs it with the bundled engines.
- The **2docx** and **2pptx** workspaces accept several files, a selected folder or a dropped
  folder, and remote `markweave convert` collects the local images referenced by a Markdown path.
  Both clients package the Markdown and its referenced images in the approved archive layout before
  upload; the service still applies every archive, path, image, size, count and scanning rule.
- An optional, permission-gated Composer workspace foundation: secure instance and personal model
  connections, administrator **LLM settings**, chat-driven proposals and questions with human
  accept or reject decisions, immutable Markdown and source revisions with native DOCX and PPTX
  revision previews, permissioned author knowledge, and typed DOCX template filling. Composer ships
  disabled by default; enabling model access requires `MARKWEAVE_COMPOSER_ENABLED=true` and the
  documented policy settings, which an administrator policy can narrow but not expand. Composer is
  not yet qualified as a complete product: its Office editing tools, complete cross-cutting
  acceptance and the 0.8.0 minor release remain pending. Migrations 20 to 26 add its persistent
  state.
- Inline, display and malformed TeX math are documented and covered by DOCX, PDF and PowerPoint
  golden tests; the final-image smoke fails when LibreOffice Math is missing.

### Changed

- `markweave restore` accepts recovery sets created by an earlier supported schema. It verifies a
  set against the schema of its recorded revision, migrates it to the current head and verifies
  stable object references again. Supported sets start at schema revision `20260829_14`
  (release 0.5.0); older, unknown or newer revisions are refused with stable errors.
- The Compose quickstart files are consolidated from ten to seven. The Next.js browser tier is part
  of `compose.yaml`, and the Podman browser settings are part of `compose.podman.yaml` and
  `compose.podman-trusted-upstream.yaml`; `compose.nextjs.yaml`, `compose.nextjs-podman.yaml` and
  `compose.nextjs-podman-trusted-upstream.yaml` are removed. Every supported helper mode renders the
  same configuration as before.
- Admission, workers and job traceability record Mermaid CLI `11.17.0`, the version the final image
  ships, and the final-image smoke compares every recorded engine version with its executable.
- User and session settings share one navigation entry, and the primary header and Administration
  setup are more compact.
- The documentation index exposes a conversion capability matrix, and the package metadata
  describes the PowerPoint and experimental document-to-Markdown workflows.
- Container images adopt the reviewed Red Hat UBI OpenSSL `3.5.8-2.el9_8` package update.

### Fixed

- Composer decisions no longer fail with HTTP 412 when a model step commits a new proposal or
  question while the workspace loads; the workspace reads one version-consistent draft snapshot.
- Restore verification accepts a missing upload only when its retention cleanup completed; a
  missing active, unexpired or cleanup-incomplete upload still fails verification.
- Distributed restore inserts template version pointers in dependency order with integrity triggers
  enabled, and standalone restore accepts recovery-set paths containing `#`, `?` or `%`.

### Known limitations

- A distributed recovery set that contains a retained conversion job referencing a superseded
  template version cannot be restored yet.

### Release notes

- Repository Compose and quickstart defaults remain pinned to the verified `0.7.3` backend and
  frontend digests until the `0.7.4` images are published and their exact receipts are adopted.

<a id="release-0-7-3"></a>

## [0.7.3] - 2026-09-22

### Fixed

- Reverse-recovery qualification selects the exact synthetic first attempt before fault injection,
  preserving strict runtime identity checks when several conversions are queued.

### Release notes

- Continues the incomplete 0.7.2 release with a fresh matched image set. The 0.7.2 Python package
  remains published; its container qualification failed before image publication or retention.
  Deployments remain on the verified 0.7.1 image digests until 0.7.3 publication and adoption pass.

<a id="release-0-7-2"></a>

## [0.7.2] - 2026-09-22

### Added

- Experimental structured `.pptx` extraction can produce slide-oriented Markdown or the documented
  Marp subset through the API, browser, and CLI. Presenter notes and normalized images default to
  enabled for structured modes; meaningful unsupported presentation content receives warnings or
  placeholders. Ordinary anydoc extraction remains the default.

### Changed

- Conversion history supports output-family filtering through the API and browser.
- Reverse-job traceability persists structured extraction options in migration 19.

### Fixed

- CLI reverse waits preserve their deadline classification at the final HTTP poll.
- Reverse reconciliation uses a consistent inventory snapshot during verification.
- Reverse workers resume interrupted attempts only after durable termination acknowledgement is
  ordered ahead of recovery.

<a id="release-0-7-1"></a>

## [0.7.1] - 2026-09-20

### Changed

- Split oversized broker, runtime, reverse-job persistence and private anydoc
  compatibility modules into explicit responsibilities while preserving public
  imports, conversion output, security checks and database transaction boundaries.

<a id="release-0-7-0"></a>

## [0.7.0] - 2026-09-20

### Fixed

- Progress-to-download transitions preserve the status column height.

- File selection displays the selected document consistently for picker and drag/drop in every conversion workspace.
- Download buttons match the primary conversion action; recent history shows source names with conversion identifiers in tooltips.
- PowerPoint slide settings start collapsed and can be expanded on demand.

### Added

- Markweave branding and a dedicated browser tab icon.

- The `2pptx` workspace generates editable PowerPoint from Markdown, a documented Marp
  subset or ZIP assets, with optional PowerPoint templates, outline preview and source bundles.
  Omitted templates use Pandoc’s native reference presentation.

### Changed

- Navigation tabs are now `2docx`, `2md`, and `templates`.
- The primary navigation shows the Markweave release version directly below the `eave` suffix of the product name.
- Recent conversion lists hide expired conversions, including jobs whose updated
  status reports expiration while the workspace is open.

<a id="release-0-6-4"></a>

## [0.6.4] - 2026-09-20

### Fixed

- Release commands reliably reap orphaned subprocesses and clean up their process groups.
- The Kubernetes WebSocket adapter supports the updated SDK protocol and channel state.

### Changed

- Reverse persistence and broker codecs are split into smaller components.
- Local distributed-test services have an automated setup, and asset permission tests work
  across checkout locations.

<a id="release-0-6-3"></a>

## [0.6.3] - 2026-09-19

### Changed

- Navigation tabs are now `md 2 docx`, `x 2 md`, and `template docx`. Reverse
  conversion retains its Experimental badge; routes and supported outputs are unchanged.
- The reverse-service unavailable message now identifies a service/configuration problem
  instead of suggesting an unsupported uploaded document.

### Fixed

- Reverse job polling and timer cleanup no longer raise browser `Illegal invocation` errors.
- The container inventory pin now matches the reviewed UBI Mesa RPM update.
- Reverse-worker mTLS certificate pins now load from their documented JSON environment value.
  Forward and reverse uploads remain independent, with navigation and late-response regressions.

<a id="release-0-6-2"></a>

## [0.6.2] - 2026-09-11

### Added

- An experimental reverse-conversion workflow converts supported office documents to Markdown
  with bounded asset extraction through the browser, HTTP API, and CLI.
- Durable reverse jobs run through an authenticated rootless Podman isolation broker, with an
  optional Kubernetes backend and node-level runtime attestation foundation.
- Public security and support policies document vulnerability reporting and supported releases.

### Changed

- JavaScript tooling now uses one deterministic pnpm workspace for the frontend and toolchain.
- Reverse queue metrics, audit events, recovery state, cancellation, and result retention are
  available in both standalone and distributed storage profiles.

### Fixed

- Rootless Podman quickstarts route the browser and API correctly from the host without weakening
  their loopback-only exposure.
- Mutation campaigns retain exact idle-expiry coverage while avoiding out-of-scope generated
  mutants.

<a id="release-0-6-1"></a>

## [0.6.1] - 2026-09-03

### Fixed

- Frontend release images now retain their exact OCI config identity through staging, allowing the
  paired backend/frontend supply-chain evidence to be verified before registry publication.
- Final-image admission and simple Podman Compose lifecycle checks now use deterministic dedicated
  connections and private-socket teardown behavior.

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
- [0.6.4](#release-0-6-4)
- [0.6.3](#release-0-6-3)
- [0.6.2](#release-0-6-2)
- [0.6.1](#release-0-6-1)
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
