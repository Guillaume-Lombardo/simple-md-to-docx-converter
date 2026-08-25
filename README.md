# Markdown to DOCX and PDF Converter

This repository contains the foundation for a service that will convert Markdown documents to
DOCX and PDF. It currently provides local authentication, administrative account management,
revocable sessions, health endpoints, and durable standalone or distributed storage profiles.
It also provides a versioned template API with ownership, visibility-aware search, preferences,
full T10 Pandoc/LibreOffice activation, immutable font-validation evidence,
safe downloads, optimistic concurrency, immutable history, restoration, audit, and guarded
deletion across both storage profiles. The internal conversion component now validates standalone Markdown or bounded ZIP
packages, binds and normalizes approved local images, sanitizes and rasterizes untrusted SVG,
renders bounded Mermaid diagrams through local sandboxed Chromium, runs Pandoc in an isolated
workspace, and converts validated DOCX to bounded PDF through an isolated LibreOffice profile.
The versioned API persists owner-scoped conversion requests, exposes deterministic job state,
supports idempotent submission and cancellation, and uses lease-owning embedded or external worker
loops over SQLite or PostgreSQL. The authenticated server-rendered conversion page now provides
upload and drag-and-drop, active-template search with preferred/fallback visibility, DOCX/PDF/both
selection, asynchronous status and cancellation, expiration handling, and safe downloads. The
template administration page provides owner and administrator lifecycle controls, preferences,
version history, and an administrator-only local-account tab. Configurable quotas and resource
policy are assembled. Structured JSON logs, durable request-to-worker correlation, low-cardinality
queue and worker metrics, bounded immutable-audit reads, version traceability, and cheap profile
readiness are available; final-container features remain under development.

The project is licensed under [Apache-2.0](LICENSE). The approved first public version is `0.3.0`,
published on PyPI as `markweave` with the matching public Python import `markweave`. See
[the release process](docs/releasing.md) for the protected PyPI and GHCR publication contract and
required one-time external configuration. A reviewed version-change pull request merged to
protected `main` starts the automatic release; repository edits without a version transition do not.

## Requirements

- [`uv`](https://docs.astral.sh/uv/)
- A platform supported by the Python 3.14 distribution managed by `uv`
- Pandoc 3.10.2 for real DOCX conversion, Mermaid CLI 11.16.0 with Chrome 151.0.7922.173 for
  diagram rendering, LibreOffice 26.2.5.2 for PDF conversion, and the Cairo shared library for SVG
  rasterization; the validated UBI toolchain image provides these engines
- A reachable ClamAV `clamd` service for fail-closed pre-persistence upload scanning

`uv` reads `.python-version`, installs Python 3.14 when needed, and creates the project environment.
No manually managed virtual environment or direct `pip` invocation is required.

## Set up the project

```bash
git clone git@github.com:Guillaume-Lombardo/simple-md-to-docx-converter.git
cd simple-md-to-docx-converter
uv sync --all-groups
```

Run the canonical local checks:

```bash
uv run ruff format .
uv run ruff check .
uv run ty check
npm ci --ignore-scripts
npm run test:web
uv run pytest -m "not requires_pandoc and not requires_mermaid and not requires_libreoffice"
uv run pytest
```

These Pytest commands independently enforce at least 90% overall coverage and 90% branch-only
coverage for the `markweave` application package. Pull-request CI also enforces 90% coverage of
changed executable application lines. Tests use `pytest-mock`; direct imports from `unittest.mock`
are rejected.

The lock file is committed. Use `uv sync --locked --all-groups` to require the committed dependency
resolution. Build dependencies are a locked project group and are also constrained explicitly for
isolated builds. Build distributions with the generated, hash-checked constraints:

```bash
uv build --build-constraint build-constraints.txt --require-hashes
```

## Repository map

- `src/markweave/`: installable Python package
- `tests/`: automated tests
- `build-constraints.txt`: hash-checked constraints exported from the build dependency group
- `docs/architecture.md`: target architecture and component boundaries
- `docs/authentication.md`: local accounts, sessions, configuration, and current limitations
- `docs/storage-profiles.md`: profile configuration, backup, and restore procedures
- `docs/jobs.md`: conversion API, durable state machine, queue, worker, and recovery contract
- `docs/resource-policy.md`: quotas, budgets, retention, recovery, and cleanup configuration
- `docs/container-deployment.md`: final-image build, hardening, runtime profiles, and SBOM scans
- `docs/observability.md`: JSON logging, correlation, metrics, audit, and readiness contracts
- `docs/releasing.md`: version, tag, PyPI Trusted Publishing, GHCR, SBOM, and provenance procedure
- `docs/conversion-ui.md`: authenticated conversion-page workflow, security, and validation scope
- `docs/administration-ui.md`: template-owner and administrator browser workflows
- `docs/templates.md`: template identity, visibility, selection, and T15 boundaries
- `docs/golden-testing.md`: reference corpus and deterministic DOCX/PDF comparison helpers
- `docs/pandoc-docx.md`: approved Markdown dialect and isolated Pandoc DOCX boundary
- `docs/archive-images.md`: secure archive and local-image preparation contract
- `docs/mermaid.md`: bounded local Mermaid/Chromium preprocessing contract
- `docs/word-templates-fonts.md`: bounded template activation and pinned font contract
- `docs/pdf-conversion.md`: isolated LibreOffice PDF and traceability contract
- `docs/local-development.md`: detailed local workflow
- `tickets/`: repository-reviewed project ticket mirrors

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change. The normative product decisions
and delivery plan are in [docs/product-specification.md](docs/product-specification.md).
