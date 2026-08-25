# Documentation

Markweave converts Markdown to DOCX, PDF, or both through a browser interface or an asynchronous
HTTP API. Start with the guide for your role:

- [User guide](user-guide.md): sign in, choose templates, submit conversions, cancel work, and
  download results.
- [API guide](api-guide.md): authentication, CSRF protection, conversion and template endpoints,
  idempotency, errors, and result retrieval.
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

Format-specific and security details are documented in [conversion jobs](jobs.md),
[authentication](authentication.md), [resource policy](resource-policy.md),
[observability](observability.md), [archives and images](archive-images.md),
[Pandoc DOCX](pandoc-docx.md), [Mermaid](mermaid.md), [PDF conversion](pdf-conversion.md), and
[Word templates and fonts](word-templates-fonts.md).

The [product specification](product-specification.md) is normative for product, architecture,
security, deployment, and acceptance decisions. The guides explain the implemented system; they do
not replace that specification or approve values that section 14 leaves unresolved.
