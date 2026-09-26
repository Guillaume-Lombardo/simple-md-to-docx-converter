<p align="center"><img src="web/public/markweave-logo.png" alt="Markweave" width="320"></p>

# Markweave: Markdown to DOCX, PDF and PowerPoint

Markweave turns a Markdown file into DOCX, PDF, or an editable PowerPoint presentation from a small browser interface. It keeps
your Word and PowerPoint templates and completed jobs on local persistent storage. It scans every upload with
ClamAV by default and can explicitly delegate that boundary to a trusted upstream proxy.

Experimental **2md** converts supported office documents and text PDFs back to Markdown, including
slide-oriented Markdown or Marp from edited PPTX files. It requires a separately configured
[external broker](docs/reverse-broker-deployment.md). See the
[capability matrix](docs/index.md#conversion-capabilities) for inputs, outputs, limits and guides.

The project is licensed under [Apache-2.0](LICENSE). The source package version is `0.7.3`.
The default public quickstart pins the matched published `0.7.3` backend and Next.js frontend
images by their verified registry digests. Do not substitute candidate or inferred digests;
candidate testing may override both image references together with another matched immutable pair.

The [documentation index](docs/index.md) provides longer guides organized by role. You do not need
to read them before trying the local profile.

## Try it locally

You need OpenSSL, `flock`, about 6 GiB of available memory, and Docker Compose or
rootless Podman with a Compose provider. Published images support Linux/AMD64.

```bash
git clone https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter.git
cd simple-md-to-docx-converter
scripts/quickstart-simple.sh up
scripts/quickstart-simple.sh password
```

Open <http://localhost:8080> and sign in as `admin` with the displayed password.
In **2docx**, upload `examples/quickstart-source.md`, keep **Pandoc default**,
choose DOCX, PDF, or both, and start the conversion. Download the result when ready.
The first startup can take several minutes while ClamAV loads its signatures.

For editable PowerPoint, open **2pptx** and follow the [PowerPoint guide](docs/powerpoint.md).
The ordinary quickstart supports both forward workflows; **2md** requires the external broker.
The currently pinned published 0.7.3 images keep Composer model setup disabled. An explicitly
supplied, matched Composer-capable backend/frontend pair can opt in with
`MARKWEAVE_SIMPLE_COMPOSER_SETUP=true`; the helper then includes
`compose.simple-composer.yaml`. Its administrator can
then open **Administration → LLM settings** to approve an HTTPS destination, enter a write-only
credential, select and test a model, and grant access. The candidate quickstart keeps its Composer
key in a separate persistent private volume. See the
[administration guide](docs/administration-ui.md#llm-settings).

```bash
scripts/quickstart-simple.sh ps
scripts/quickstart-simple.sh logs
scripts/quickstart-simple.sh down
```

Normal shutdown preserves accounts, templates, results and the administrator password.
Results in this evaluation profile expire after 10 minutes.

This is a **local evaluation profile**, bound to loopback. Its disposable `/work` volume
has no physical capacity cap. Do not expose it as a production service. The
[quickstart operations guide](docs/quickstart.md) explains the bounded secure profile,
Docker/Podman selection, custom ports and HTTPS proxy origins, Word templates, recovery,
and the explicit trusted-upstream and insecure SSH-tunnel modes with their restrictions.

## Use and operate Markweave

- [Command-line interface](docs/cli.md)
- [Python package and optional dependencies](docs/python-distribution.md)
- [Provision users from a startup CSV and require password renewal](docs/authentication.md#startup-csv-provisioning)
- [Conversion interface](docs/conversion-ui.md)
- [Composer workspace, author directory, and typed Word filling](docs/composer.md)
- [PowerPoint and Marp workflow](docs/powerpoint.md)
- [Template administration](docs/administration-ui.md)
- [Supported Markdown and DOCX behavior](docs/pandoc-docx.md)
- [Word templates and fonts](docs/word-templates-fonts.md)
- [Jobs, cancellation, retention, and recovery](docs/jobs.md)
- [Logs, metrics, audit, and readiness](docs/observability.md)
- [Release and image update process](docs/releasing.md)
- [Upgrade and rollback guidance](docs/upgrading.md)
- [Changelog](CHANGELOG.md)
- [Security policy](SECURITY.md)
- [Support policy](SUPPORT.md)

## How it works

The browser and HTTP API authenticate a local user, validate and scan the upload, and record a
durable conversion job. A worker claims that job with a renewable lease, resolves the exact
immutable template version when one was selected (or uses Pandoc's built-in default reference
document), and runs the local Pandoc, Chromium/Mermaid, and LibreOffice engines inside bounded
workspaces. Results and traceability metadata are retained for the configured period; document
content is not written to logs.

The standalone profile used by Compose keeps SQLite metadata and atomic objects under one `/data`
volume and runs one embedded worker. The distributed profile separates API and worker processes,
using PostgreSQL and S3-compatible object storage so workers can scale independently. Both profiles
share the same authorization, queue, validation, and retention contracts. See the
[architecture guide](docs/architecture.md), [API guide](docs/api-guide.md), and
[complete configuration reference](docs/configuration.md) for the deeper design.

## Develop and contribute

Development targets Python 3.14 and uses `uv`, Ruff, `ty`, Pytest, and the repository's locked
toolchain. Install all groups, then run the canonical checks:

```bash
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run ty check
scripts/javascript/bootstrap-pnpm.sh "$PWD/.pnpm-tools"
export PATH="$PWD/.pnpm-tools/bin:$PATH"
export COREPACK_HOME="$PWD/.pnpm-tools/corepack-home" COREPACK_ENABLE_NETWORK=0
pnpm install --frozen-lockfile --ignore-scripts
pnpm run test:web
uv run pytest -m "not requires_pandoc and not requires_mermaid and not requires_libreoffice"
uv run pytest
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) and the normative
[product specification](docs/product-specification.md) before changing behavior. The
[local-development guide](docs/local-development.md) covers the repository layout and deeper setup;
the [release guide](docs/releasing.md) covers protected PyPI and GHCR publication.
