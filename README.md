# Markweave: Markdown to DOCX and PDF

Markweave turns a Markdown file into DOCX, PDF, or both from a small browser interface. It keeps
your Word templates and completed jobs on local persistent storage and scans every upload with
ClamAV before saving it.

The project is licensed under [Apache-2.0](LICENSE). Version `0.3.0` is published as the Python
package `markweave` and as the container image used below.

The [documentation index](docs/index.md) provides longer guides organized by role. You do not need
to read them before trying the local profile.

## Try it locally

You need an AMD64 Linux host, the standard local rootful Docker Engine daemon at
`unix:///var/run/docker.sock` with Compose, OpenSSL, `mkfs.ext4`, `losetup`, `sudo`, and about 6 GiB
of available memory. `DOCKER_HOST` and remote or non-default Docker contexts are unsupported.
Docker Desktop and rootless Docker, non-Linux hosts, and native ARM are also unsupported by this
loop-backed quickstart. Clone the repository so Compose can use the reviewed Chromium seccomp
profile, then run its setup command:

```bash
git clone https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter.git
cd simple-md-to-docx-converter
scripts/quickstart.sh up
```

The script explains and requests `sudo` before it creates and attaches an exact 256 MiB ext4 loop
filesystem for disposable work; Docker Engine mounts it while the application itself remains an
unprivileged container process. The script creates the administrator password once under the
current user's private state directory, reuses it on later starts, and never redirects a secret to
a predictable path. It also decodes the committed
`examples/quickstart-template.docx.base64` fixture into that private directory. Show the password
without putting it in shell history:

```bash
scripts/quickstart.sh password
```

Open <http://localhost:8080>, sign in as `admin`, and use the password above. The first start can
take several minutes while ClamAV downloads and loads its signatures;
`scripts/quickstart.sh ps` shows when both services are healthy, and
`scripts/quickstart.sh logs` follows the signature-download progress.

To make a first conversion:

1. Open **Templates**, create a template, and select the generated template at the path printed by
   the setup script (normally `~/.local/state/markweave-quickstart/quickstart-template.docx`).
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

Stop the evaluation with:

```bash
scripts/quickstart.sh down
```

The shutdown command validates the work volume's exact Compose labels before removing only that
volume. It discovers a live loop device from the private backing file, never from a stale
`/dev/loopN` recorded in Docker metadata, and detaches it only after confirming the backing-file
identity. It then deletes the disposable 256 MiB image. `markweave-data`, `clamav-signatures`, the
administrator password, and the decoded template remain, so accounts, templates, jobs, results,
signatures, and credentials survive normal shutdown. Do not add the `--volumes` option unless you
intentionally want Docker to remove the durable local data.

The Compose services do not automatically restart with the Docker daemon because a loop-device
number is not stable across a host reboot. After an abnormal stop or reboot, run
`scripts/quickstart.sh up` again. It validates the exact project/volume labels, removes stale
scratch metadata, resolves the private backing file independently of any old device number,
reformats the disposable filesystem, and then restarts the stack. A repeated `up` while the service
is already running validates and reuses its current filesystem without reformatting it. The `down`
command can also clean stale scratch metadata and the private image when the old loop association
has vanished; it never detaches a device that has since been reused for an unrelated file.
If Compose fails while starting—for example, because port 8080 is already occupied—the script
removes the partially created containers, loop attachment, scratch volume, and scratch image. It
retains the password, template, application data, and ClamAV signatures so correcting the conflict
and rerunning `scripts/quickstart.sh up` safely resumes the evaluation.

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
