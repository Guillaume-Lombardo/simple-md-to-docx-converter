# Markweave: Markdown to DOCX and PDF

Markweave turns a Markdown file into DOCX, PDF, or both from a small browser interface. It keeps
your Word templates and completed jobs on local persistent storage. It scans every upload with
ClamAV by default and can explicitly delegate that boundary to a trusted upstream proxy.

The project is licensed under [Apache-2.0](LICENSE). The reviewed Python package and application
version is `0.5.2`. Until its post-publication pin update completes, the quickstart remains pinned
to the verified immutable `0.5.1` image digest.

The [documentation index](docs/index.md) provides longer guides organized by role. You do not need
to read them before trying the local profile.

## Try it locally

You need OpenSSL, `flock` from util-linux, about 6 GiB of available memory, and either Docker Engine
with Compose or rootless Podman with a working `podman compose` provider. The published Markweave
image is Linux/AMD64; native ARM is not supported. Clone the repository so Compose can use the
reviewed Chromium seccomp profile, then choose one of these local-evaluation paths.

| Path | Runtime | Command | `/work` isolation |
| --- | --- | --- | --- |
| Simple | Docker Compose | `scripts/quickstart-simple.sh up` | Named volume; no physical cap |
| Simple | Rootless Podman Compose | `MARKWEAVE_SIMPLE_RUNTIME=podman scripts/quickstart-simple.sh up` | Named volume; no physical cap |
| Trusted upstream | Rootless Podman Compose | `MARKWEAVE_SIMPLE_RUNTIME=podman scripts/quickstart-simple.sh up --trust-upstream-antivirus` | Named volume; no physical cap |
| Insecure SSH-tunnel test | Docker or rootless Podman Compose | `scripts/quickstart-simple.sh up --insecure` | Named volume; no physical cap |
| Secure | Rootful Docker Compose only | `scripts/quickstart.sh up` | Exact 256 MiB ext4 filesystem |

The simple path needs no `sudo` and is the easiest way to try Markweave:

```bash
git clone https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter.git
cd simple-md-to-docx-converter
scripts/quickstart-simple.sh up
```

The helper automatically prefers a working Docker Compose installation and otherwise selects
rootless Podman Compose. Set `MARKWEAVE_SIMPLE_RUNTIME=docker` or
`MARKWEAVE_SIMPLE_RUNTIME=podman` to select one deterministically. It initializes the named
workspace for application UID 1001 in a short-lived, network-isolated container; the application
itself still starts as UID 1001 without capabilities. The rootless Podman-compatible application
image and the simple Podman Compose workflow do not make the secure loop-device helper compatible
with Podman. For Podman, the helper starts a private API service configured with the reviewed
Chromium seccomp profile because Docker Compose cannot pass that local profile directly to Podman's
Docker-compatible API. Podman's automatic Docker-API health metadata is not used; the helper polls
ClamAV directly before starting Markweave, then polls Markweave's local readiness endpoint with a
bounded timeout.

For a temporary test on a remote server reached only through an SSH tunnel, use the explicit
insecure mode on that server:

```bash
MARKWEAVE_SIMPLE_RUNTIME=podman MARKWEAVE_SIMPLE_PORT=11279 \
  scripts/quickstart-simple.sh up --insecure
```

Then create the tunnel from the browser machine with
`ssh -L 11279:127.0.0.1:11279 user@server` and open <http://localhost:11279>. This mode neither
starts ClamAV nor validates login origins, so `localhost`, `127.0.0.1`, a tunneled hostname, and
browser requests serialized as `Origin: null` can reach authentication. It still publishes only
on server loopback (`127.0.0.1`) and verifies the disabled-origin behavior before reporting
readiness. **Never bind or proxy this mode to a network, and never use it in production.** Stop it
with `scripts/quickstart-simple.sh down` as soon as the test is complete.

Set `MARKWEAVE_SIMPLE_PORT` to publish the simple quickstart on another loopback port. The helper
also configures that exact public origin so browser login keeps its strict same-origin check:

```bash
MARKWEAVE_SIMPLE_RUNTIME=podman MARKWEAVE_SIMPLE_PORT=11279 \
  scripts/quickstart-simple.sh up --trust-upstream-antivirus
```

Open <http://localhost:11279>; using a different hostname or IP address is intentionally rejected
by the login origin check in the normal and trusted-upstream modes. Before reporting readiness,
the simple helper verifies both the public
origin received by the container and a browser-equivalent login request carrying that origin. If
the selected Compose provider drops or rewrites the value, startup fails instead of leaving a
running stack whose login form always returns `LOGIN_ORIGIN_INVALID`.

When a same-host reverse proxy exposes Markweave through HTTPS, set `MARKWEAVE_PUBLIC_ORIGIN` to
the exact browser-visible origin (scheme, host, and optional port, without a path):

```bash
MARKWEAVE_SIMPLE_RUNTIME=podman MARKWEAVE_SIMPLE_PORT=11279 \
MARKWEAVE_PUBLIC_ORIGIN=https://converter.example \
  scripts/quickstart-simple.sh up --trust-upstream-antivirus
```

The proxy can forward to `http://127.0.0.1:11279`; Markweave validates login against the configured
public origin instead of trusting forwarded-host headers. In trusted-upstream antivirus mode, the
proxy must also scan every upload and remain the only route to Markweave.

If an upstream proxy already scans every upload, the Podman quickstart can omit ClamAV entirely:

```bash
MARKWEAVE_SIMPLE_RUNTIME=podman \
  scripts/quickstart-simple.sh up --trust-upstream-antivirus
```

This option neither pulls nor starts the ClamAV image. It is safe only when the proxy scans every
conversion and template upload before forwarding and host firewall or network policy prevents any
direct or alternate route to Markweave. The helper and application print a warning because this is
an operator-asserted external security boundary. Never use the option merely to work around an
unavailable scanner. Under rootless Podman, this explicit mode requires `slirp4netns` and uses it
directly instead of creating CNI bridge networks. This permits the loopback-only published port on
hosts whose CNI `portmap` plugin cannot use nftables; the default ClamAV topology still requires
Podman's normal container-network support.

This uses an ordinary engine-managed named volume for disposable `/work` data. The application
still runs as a non-root user with a read-only root filesystem, no Linux capabilities,
loopback-only HTTP, pinned images, and the same memory, CPU, process, upload, and conversion limits.
However, the named volume has **no physical capacity cap**: a failed or hostile document engine
could consume host disk until the container engine or host stops it. Use the secure path below when
physical disk-exhaustion isolation matters.

The secure path creates an exact 256 MiB ext4 loop filesystem for `/work`. It additionally requires
an AMD64 Linux host, the standard local rootful Docker Engine at `unix:///var/run/docker.sock`,
`mkfs.ext4`, and `losetup`; it requests `sudo` to manage the loop device. Docker Desktop and
rootless Docker are unsupported, as are `DOCKER_HOST` and remote or non-default Docker contexts,
non-Linux hosts, and native ARM:

```bash
scripts/quickstart.sh up
```

Both scripts create the administrator password once in a private state directory and reuse it on
later starts. They also decode `examples/quickstart-template.docx.base64` beside that password.
Show the simple-path password without putting it in shell history:

```bash
scripts/quickstart-simple.sh password
```

For the secure path, use the same commands with `quickstart.sh` instead of
`quickstart-simple.sh`: `scripts/quickstart.sh password` shows its password and
`scripts/quickstart.sh down` stops it.

Open <http://localhost:8080>, sign in as `admin`, and use the password above. In the default mode,
the first start can take several minutes while ClamAV downloads and loads its signatures. The simple `up` command
returns only after the selected runtime passes its readiness checks;
`scripts/quickstart-simple.sh ps` shows the containers, and `scripts/quickstart-simple.sh logs`
follows the signature-download progress. The secure helper reports that Markweave is starting;
use `scripts/quickstart.sh ps` or `scripts/quickstart.sh logs` until both services are healthy.

To make a first conversion:

1. Open **Convert**, upload `examples/quickstart-source.md`, keep **Pandoc default** selected, and
   choose DOCX, PDF, or both.
2. Optional: open **Templates**, create a template, and select the generated template at the path
   printed by the setup script when you want custom Word styles. In **Expected fonts**, enter
   `Aptos, Aptos Display, Calibri, Cambria, Cambria Math, Consolas, Courier New, Times New Roman`.
   Return to **Convert** and select it.
3. Start the conversion. When the job says it is ready, download the result.

A tiny source file is enough to try the workflow; `examples/quickstart-source.md` contains:

```markdown
# My first document

Hello from **Markweave**.
```

Stop the evaluation with:

```bash
scripts/quickstart-simple.sh down
```

The simple shutdown validates the ordinary work volume's exact project labels, local driver, and
empty mount options before removing only that disposable volume. `markweave-data`,
`clamav-signatures` when it exists, the administrator password, and the decoded template remain, so
accounts, templates, jobs, results, signatures, and credentials survive normal shutdown. Do not add the
`--volumes` option unless you intentionally want the container engine to remove durable local data.

Completed conversion results remain downloadable for 10 minutes in this evaluation profile. This
is a local convenience value, not a recommended production retention policy.

Both paths support repeated `up`, stopped-stack restart, `ps`, `logs`, and `down`. A stopped restart
discards only the disposable work volume and preserves durable data. If Compose fails while
starting—for example, because port 8080 is occupied—the selected script removes partial containers
and its exact scratch resources while retaining the password, template, application data, and
ClamAV signatures. The simple helper rejects concurrent commands that could race over the same
private state or Podman service; wait for the active command to finish before running another.

The secure services do not automatically restart with Docker because loop-device numbers are not
stable across a reboot. After an abnormal stop or reboot, run `scripts/quickstart.sh up`; it removes
stale scratch metadata, resolves the private backing file independently of old device numbers,
reformats disposable work, and restarts safely. Cleanup resolves the current device from the
private backing file, never from a stale device number. It refuses a loop device reused for an unrelated file.

## What this Compose profile is—and is not

The secure base `compose.yaml` is a bounded standalone evaluation profile. The simple
`compose.simple.yaml` overlay deliberately replaces only its bounded `/work` mount with an
unbounded named volume. In both paths, one rootless Markweave process runs the API and embedded
worker, `/data` is persistent, the root filesystem is read-only, and the browser port binds only
to `127.0.0.1`. In the default mode, ClamAV has persistent signatures and no host port; its scanner
network is internal, and only ClamAV joins the network used to refresh signatures. The
trusted-upstream overlay removes the application dependency and keeps the ClamAV service inactive.

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

- [Provision users from a startup CSV and require password renewal](docs/authentication.md#startup-csv-provisioning)
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
npm ci --ignore-scripts
npm run test:web
uv run pytest -m "not requires_pandoc and not requires_mermaid and not requires_libreoffice"
uv run pytest
```

Read [CONTRIBUTING.md](CONTRIBUTING.md) and the normative
[product specification](docs/product-specification.md) before changing behavior. The
[local-development guide](docs/local-development.md) covers the repository layout and deeper setup;
the [release guide](docs/releasing.md) covers protected PyPI and GHCR publication.
