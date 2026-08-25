# Markweave: Markdown to DOCX and PDF

Markweave turns a Markdown file into DOCX, PDF, or both from a small browser interface. It keeps
your Word templates and completed jobs on local persistent storage and scans every upload with
ClamAV before saving it.

The project is licensed under [Apache-2.0](LICENSE). Version `0.3.0` is published as the Python
package `markweave` and as the container image used below.

The [documentation index](docs/index.md) provides longer guides organized by role. You do not need
to read them before trying the local profile.

## Try it locally

You need Docker Engine with Compose, OpenSSL for one password-generation command, and about 5 GiB
of available memory. The published Markweave image is currently Linux/AMD64 only; this quickstart
does not claim native ARM support. Clone the repository so Compose can use the reviewed Chromium
seccomp profile:

```bash
git clone https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter.git
cd simple-md-to-docx-converter
export MARKWEAVE_INITIAL_ADMIN_PASSWORD="$(openssl rand -hex 24)"
docker compose up -d
```

Keep that terminal open until you have signed in, or record the generated password in your local
password manager. Check it without copying it into shell history:

```bash
printf '%s\n' "$MARKWEAVE_INITIAL_ADMIN_PASSWORD"
```

Open <http://localhost:8080>, sign in as `admin`, and use the password above. The first start can
take several minutes while ClamAV downloads and loads its signatures; `docker compose ps` shows
when both services are healthy, and `docker compose logs -f clamav` shows download progress.

To make a first conversion:

1. Open **Administration**, create a template, and upload a trusted `.docx` whose styles you want
   Markweave to reuse. Enter every font used by that file in **Expected fonts**. Template activation
   deliberately fails if the file, styles, relationships, or font declaration are unsafe or
   incomplete.
2. Return to **Convert**, upload a Markdown file, select your active template, and choose DOCX, PDF,
   or both.
3. Start the conversion. When the job says it is ready, download the result.

A tiny source file is enough to try the workflow:

```markdown
# My first document

Hello from **Markweave**.
```

Stop the containers with `docker compose down`. The `markweave-data` and `clamav-signatures` named
volumes survive that command, so accounts, templates, jobs, and antivirus signatures remain. Do
not add `--volumes` unless you intentionally want Docker to remove that local data.

## What this Compose profile is—and is not

`compose.yaml` is a bounded standalone evaluation profile: one rootless Markweave process runs the
API and embedded worker, `/data` is persistent, writable scratch space is bounded, the root
filesystem is read-only, and the browser port binds only to `127.0.0.1`. ClamAV has persistent
signatures and no host port. The scanner network is internal, the browser-facing bridge disables
IP masquerading, and only ClamAV joins the network used to refresh signatures.

The Compose profile is not a production deployment. Its upload, queue, memory, retention, and
timeout values are local evaluation limits reused from the tested final-image workflow. Do not
publish port 8080 or place this HTTP setup on an untrusted network. A production deployment needs
reviewed limits, TLS, secrets management, backups, network policy, monitoring, and the standalone
or distributed topology appropriate to its workload. Start with the
[container deployment guide](docs/container-deployment.md), [resource policy](docs/resource-policy.md),
[storage profiles](docs/storage-profiles.md), and [authentication guide](docs/authentication.md).

Both images are pinned by digest. The ClamAV image uses the supported `1.4_base` line with its
database stored in `clamav-signatures`; review release notes and validation evidence before
changing either digest. ClamAV recommends roughly 4 GiB of RAM for reliable operation and explains
the `_base` image and persistent database pattern in its
[official Docker documentation](https://docs.clamav.net/manual/Installing/Docker.html).

## Use and operate Markweave

- [Conversion interface](docs/conversion-ui.md)
- [Template administration](docs/administration-ui.md)
- [Supported Markdown and DOCX behavior](docs/pandoc-docx.md)
- [Word templates and fonts](docs/word-templates-fonts.md)
- [Jobs, cancellation, retention, and recovery](docs/jobs.md)
- [Logs, metrics, audit, and readiness](docs/observability.md)
- [Release and image update process](docs/releasing.md)

## How it works

The browser and HTTP API authenticate a local user, validate and scan the upload, and record a
durable conversion job. A worker claims that job with a renewable lease, resolves the exact
immutable template version, and runs the local Pandoc, Chromium/Mermaid, and LibreOffice engines
inside bounded workspaces. Results and traceability metadata are retained for the configured
period; document content is not written to logs.

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
npm ci --ignore-scripts
npm run test:web
uv run pytest -m "not requires_pandoc and not requires_mermaid and not requires_libreoffice"
uv run pytest
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) and the normative
[product specification](docs/product-specification.md) before changing behavior. The
[local-development guide](docs/local-development.md) covers the repository layout and deeper setup;
the [release guide](docs/releasing.md) covers protected PyPI and GHCR publication.
