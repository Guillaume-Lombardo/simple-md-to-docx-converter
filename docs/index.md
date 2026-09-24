# Documentation

Markweave converts Markdown to DOCX, PDF, and editable PowerPoint, and converts supported
documents back to Markdown through its experimental reverse workflow. Use the browser, HTTP API,
or installed CLI.

## Conversion capabilities

| Input | Output | Availability and limits | Guide |
| --- | --- | --- | --- |
| Markdown, or ZIP with Markdown and local assets | DOCX, PDF, or ZIP containing both | Available in the ordinary quickstart (**2docx**). Optional Word template; PDF is rendered from DOCX. No remote resources or raw HTML. | [Document conversion](user-guide.md#convert-a-document) |
| Markdown or the supported Marp subset, optionally with ZIP assets | Editable PPTX, or ZIP with PPTX and original source | Available in the ordinary quickstart (**2pptx**). Optional PowerPoint template. No arbitrary CSS/themes; check slides for overflow. Original-source recovery does not include later PowerPoint edits. | [PowerPoint and Marp](powerpoint.md) |
| Word, PowerPoint, Excel, OpenDocument, RTF, EPUB, CSV, or text PDF | Markdown, or ZIP with Markdown, assets and manifest | Experimental **2md**; requires the external broker. Accepted extensions and upload limits come from the service. No OCR or hosted fallback; PDF is text-only, without images or layout. | [Document to Markdown](user-guide.md#revert-a-document-to-markdown) |
| Edited PPTX | Slide-oriented Markdown or Marp, with optional notes and images | Experimental **2md** with the external broker. Opt-in extraction; no original-source recovery or full visual round trip. | [Edited PowerPoint extraction](user-guide.md#extract-an-edited-powerpoint-presentation) |

The ordinary quickstarts do not configure reverse execution. To enable **2md**, an operator must
configure the [external isolation broker](reverse-broker-deployment.md) and a matched immutable
reverse-attempt image. Installing the Python package alone does not provide that deployment.
All conversions use bounded uploads, asynchronous jobs, and expiring results; deployment-specific
limits are documented in the [configuration reference](configuration.md).

## Guides by role

Start with the guide for your role:

- [Quickstart operations](quickstart.md): local profiles, runtime selection, origins and recovery.
- [User guide](user-guide.md): sign in, choose templates, submit conversions, cancel work, and
  download results.
- [Composer workspace](composer.md): guided messages, reviewed proposals, exact revisions, and
  private document previews when an authorized model connection is available.
- [API guide](api-guide.md): authentication, CSRF protection, conversion and template endpoints,
  idempotency, errors, and result retrieval.
- [Command-line interface](cli.md): stable output, profiles, remote command families, and local
  operational commands.
- [Python distribution](python-distribution.md): base installation, optional dependency groups,
  public imports, and package verification.
- [Template administration](templates.md): immutable versions, visibility, preferences, fallback,
  archive, restore, and deletion.
- [Account and template UI](administration-ui.md): administrator workflows in the browser.
- [Operations](operations.md): readiness, metrics, logs, queue handling, retention, and safe drain.
- [Configuration reference](configuration.md): exact environment settings, defaults, and
  cross-field constraints.
- [Storage profiles](storage-profiles.md): standalone SQLite/filesystem and distributed
  PostgreSQL/S3-compatible storage.
- [Backup and recovery](recovery.md): consistent backup sets, restore exercises, RPO, and RTO.
- [Container deployment](container-deployment.md): runtime modes, Kubernetes fragments, rootless
  hardening, TLS, secrets, network policy, and immutable images.
- [Architecture](architecture.md): component boundaries, data flow, security, and profile topology.
- [Local development](local-development.md): toolchain, tests, CI, and dependency changes.
- [Agent workflow](agent-workflow.md): repository-specific process for automated contributors.
- [Release process](releasing.md): versioning, publication, provenance, and post-release checks.
- [Upgrade and rollback](upgrading.md): supported transitions, backups, schema handling, and
  configuration migration.
- [Changelog](../CHANGELOG.md): released user-visible, operational, security, and compatibility
  changes.
- [Security policy](../SECURITY.md): private reporting, supported releases, and disclosure.
- [Support policy](../SUPPORT.md): help channels, safe diagnostic information, and support scope.
- [Reverse-conversion broker](reverse-broker-deployment.md): experimental document-to-Markdown
  isolation, worker transport, and operational boundaries.
- [Optional Kubernetes reverse isolation](kubernetes-reverse-isolation.md): dedicated-node policy,
  runtime attestation, deployment assets, and the remaining production-acceptance requirement.

Format-specific and security details are documented in [conversion jobs](jobs.md),
[authentication](authentication.md), [resource policy](resource-policy.md),
[observability](observability.md), [archives and images](archive-images.md),
[Pandoc DOCX](pandoc-docx.md), [Mermaid](mermaid.md), [PDF conversion](pdf-conversion.md), and
[Word templates and fonts](word-templates-fonts.md).

Release-readiness work uses the executable
[cross-surface qualification matrix](evidence/t50-cross-surface-qualification.md), which records
the exact commands, prerequisites, artifacts, and residual limitations without selecting a release
version.

The [product specification](product-specification.md) is normative for product, architecture,
security, deployment, and acceptance decisions. The guides explain the implemented system; they do
not replace that specification or approve values that section 14 leaves unresolved.
