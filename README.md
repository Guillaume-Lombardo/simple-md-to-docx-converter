# Markweave: Markdown to DOCX and PDF

Markweave turns a Markdown file into DOCX, PDF, or both from a small browser interface. It keeps
your Word templates and completed jobs on local persistent storage and scans every upload with
ClamAV before saving it.

The project is licensed under [Apache-2.0](LICENSE). Version `0.3.0` is published as the Python
package `markweave` and as the container image used below.

The [documentation index](docs/index.md) provides longer guides organized by role. You do not need
to read them before trying the local profile.

## Try it locally

You need Docker Engine with Compose, OpenSSL for one password-generation command, and about 6 GiB
of available memory. The published Markweave image is currently Linux/AMD64 only; this quickstart
does not claim native ARM support. Clone the repository so Compose can use the reviewed Chromium
seccomp profile. Store the generated evaluation password outside the checkout so later terminals
use the same value:

```bash
git clone https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter.git
cd simple-md-to-docx-converter
umask 077
printf 'MARKWEAVE_INITIAL_ADMIN_PASSWORD=%s\n' "$(openssl rand -hex 24)" \
  > /tmp/markweave-quickstart.env
openssl base64 -d -in examples/quickstart-template.docx.base64 \
  -out quickstart-template.docx
docker compose --env-file /tmp/markweave-quickstart.env up -d
```

Record the generated password in your local password manager. Read it from the protected file in
any terminal without putting the value in shell history:

```bash
sed -n 's/^MARKWEAVE_INITIAL_ADMIN_PASSWORD=//p' /tmp/markweave-quickstart.env
```

Open <http://localhost:8080>, sign in as `admin`, and use the password above. The first start can
take several minutes while ClamAV downloads and loads its signatures;
`docker compose --env-file /tmp/markweave-quickstart.env ps` shows when both services are healthy,
and `docker compose --env-file /tmp/markweave-quickstart.env logs -f clamav` shows download progress.

To make a first conversion:

1. Open **Templates**, create a template, and select the generated `quickstart-template.docx`.
   In **Expected fonts**, enter this exact comma-separated list:
   `Aptos, Aptos Display, Calibri, Cambria, Cambria Math, Consolas, Courier New, Times New Roman`.
2. Return to **Convert**, upload `examples/quickstart-source.md`, select your active template, and
   choose DOCX, PDF, or both.
3. Start the conversion. When the job says it is ready, download the result.

A tiny source file is enough to try the workflow; `examples/quickstart-source.md` contains:

```markdown
# My first document

Hello from **Markweave**.
```

Stop the containers with
`docker compose --env-file /tmp/markweave-quickstart.env down`, then remove only the disposable
conversion workspace after validating its exact default-project identity:

```bash
test "$(docker volume inspect --format '{{ index .Labels "com.docker.compose.project" }}' \
  markweave_markweave-work)" = markweave
test "$(docker volume inspect --format '{{ index .Labels "com.docker.compose.volume" }}' \
  markweave_markweave-work)" = markweave-work
docker volume rm markweave_markweave-work
```

The application limits that disk-backed workspace to 256 MiB. `markweave-data` and
`clamav-signatures` remain, so accounts, templates, jobs, results, and antivirus signatures survive.
Do not add `--volumes` unless you intentionally want Docker to remove that durable local data.
Remove `quickstart-template.docx` and the password file when you no longer need this evaluation
deployment.

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
