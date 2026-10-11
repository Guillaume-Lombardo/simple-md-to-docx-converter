# Conversion and job CLI

The installed `markweave` client submits conversions and manages the current user's jobs only
through the authenticated `/api/v1` HTTP API. Sign in first with `markweave login`; every command
uses the selected owner-only connection profile and never opens server repositories, object stores,
or worker services.

## Submit a conversion

Submit Markdown or a ZIP archive and select DOCX, PDF, or both:

```text
markweave convert source.md --output docx
markweave convert bundle.zip --output both --idempotency-key deployment-42-document-7
```

The default output is DOCX. The client sends a canonical private upload name based only on the
`.md` or `.zip` kind; it does not print or log the local source name or document content. Successful
output includes the job identifier, durable correlation identifier, and server-provided polling
delay. Keep the correlation identifier when reporting a problem.

Omit both template options to use Pandoc's default reference document. To pin styling, provide the
visible template identity and its exact current immutable version together:

```text
markweave convert source.md \
  --template-id 11111111-1111-4111-8111-111111111111 \
  --template-version-id 22222222-2222-4222-8222-222222222222
```

An explicit idempotency key is owner-scoped and payload-sensitive. Repeating the same request with
the same key returns the original job; changing its source or parameters returns the server's
stable conflict. `--retries COUNT` retries only ambiguous network failures, accepts at most five
retries, and requires an explicit idempotency key.

Use `--profile NAME` on any remote command to select a non-default connection profile. Global
options such as `--json` and `--timeout` precede the command name.

## Inspect and control jobs

```text
markweave jobs list --offset 0 --limit 50
markweave jobs show JOB_ID
markweave --timeout 300 jobs wait JOB_ID --poll-interval 2
markweave jobs cancel JOB_ID
```

Lists are owner-scoped and bounded to 100 entries per request. `jobs wait` requires the global
positive finite `--timeout`, bounds each HTTP request by the remaining deadline, and polls no more
frequently than its positive `--poll-interval`. It succeeds only for `succeeded`; safe failed,
cancelled, expired, authorization, quota, capacity, and service errors retain their stable message
and correlation identifier.

## Download results and manifests

```text
markweave jobs download JOB_ID ./result.docx
markweave jobs manifest JOB_ID ./traceability.json
```

The destination parent must be a real directory. Downloads stream into a private temporary file,
flush it, and publish it atomically. A partial or interrupted transfer is removed. Existing paths
are refused by default; `--overwrite` may atomically replace only an existing regular file and
never follows a symlink. The result extension remains the operator's explicit destination choice.

Manifest downloads are available for successful PDF and combined jobs. DOCX-only, failed,
cancelled, unavailable, and expired jobs retain the server's safe error contract. Human output
never prints the destination or source name; JSON output reports only job, byte-count, result type,
status, and a validated correlation identifier when the response provides one.

## Convert locally without a server

`markweave convert SOURCE --local` converts your own local file on this host without the HTTP API,
an account, persistence, or ClamAV (product decision D1). It runs the same input validation,
archive, image, Mermaid, Pandoc, and LibreOffice adapters as the service, with the same
`MARKWEAVE_*` limits, inside the same per-engine network isolation. Without `--local`, `convert`
keeps its HTTP-only submission behavior; remote options such as `--output`, `--profile`,
`--template-id`, and `--idempotency-key` are refused in local mode, and local options are refused
without `--local`.

```text
markweave convert doc.md --local --to docx,pdf --template template.docx -o out/
markweave --timeout 300 convert bundle.zip --local --to pdf --require-pinned -o out/
markweave --json convert doc.md --local --overwrite -o out/
```

Local mode is meant for trusted input you own. It does not scan for malware and is not a
substitute for the service when the input is untrusted.

### Configuration

Local mode reads only the service's conversion, engine, and template variables, with the same
names, constraints, and legacy `MD_CONVERTER_*` aliases; it never reads storage, account, scanner,
or job settings. The repository provides
[`examples/local-conversion.env`](../examples/local-conversion.env) with the quickstart evaluation
values from `compose.yaml`. They are not sized production limits; review them for your workload.

```bash
set -a; . examples/local-conversion.env; set +a
export MARKWEAVE_CONVERSION_FONT_MANIFEST_PATH="$PWD/toolchain/document-engines/fonts/manifest.json"
```

The example file sets the final image's engine paths and font manifest. On a host, adjust
`MARKWEAVE_TEMPLATE_PANDOC_EXECUTABLE`, `MARKWEAVE_TEMPLATE_LIBREOFFICE_EXECUTABLE`,
`MARKWEAVE_CONVERSION_MERMAID_EXECUTABLE`, and `MARKWEAVE_CONVERSION_CHROMIUM_EXECUTABLE` as
needed. `MARKWEAVE_CONVERSION_FONT_MANIFEST_PATH` is required only for PDF output, whose
traceability records its digest. A missing, invalid, or conflicting variable fails with
`local_configuration_invalid` and names the variables, never their values.

### Inputs, outputs, and templates

`SOURCE` is a `.md` file or a `.zip` archive in the [approved layout](archive-images.md). The local
file is read without following a final symbolic link, must be a non-empty regular file, and is
bounded by `MARKWEAVE_CONVERSION_UPLOAD_MAX_BYTES`. A standalone Markdown file must not reference
local images; package it with its images as a ZIP archive. `--template` accepts a `.docx` reference
document bounded by `MARKWEAVE_TEMPLATE_MAX_ARCHIVE_BYTES`; it passes the same static package,
active-content, relationship, style, and approved-font checks as template activation. Without
`--template`, Pandoc's default reference document is used.

`--to` takes a comma-separated list of `docx` and `pdf` and defaults to `docx`. Outputs are named
after the source stem, for example `doc.docx`, `doc.pdf`, and `doc.traceability.json`, in the
existing real directory given by `-o`/`--output-dir` (default: the current directory). Outputs are
published as one set with owner-only `0600` permissions: every file is first written to a private
temporary file in that directory and synchronized, and only then are the names published. Existing
outputs are refused before conversion starts, and a name created concurrently is never replaced.
`--overwrite` replaces only existing regular files and keeps each original until the whole set is
published. A failure, the global timeout, or cancellation while files are written or published
restores the previous set (or removes the new files) and reports the failure; a signal that arrives
after the directory is synchronized no longer withdraws the result. If an original cannot be
restored, the command fails with `output_rollback_failed` and leaves the original copy in a hidden
`.markweave-local-*.previous` file.

### Engines, approved pins, and traceability

Pandoc is always required, LibreOffice only for PDF, and Mermaid CLI with Chromium only when the
document contains a Mermaid diagram. Before converting, local mode runs `ENGINE --version` with
fixed arguments inside the engine isolation. A missing or unusable required engine fails with
`engine_missing`. A version that differs from the approved pin, or cannot be determined, produces a
warning on standard error (or in the JSON `warnings` list) and is recorded in the traceability
manifest. `--require-pinned` turns that warning into an `engine_version_unpinned` failure before
any document engine runs.

The approved pins are the qualified component versions the service records for every job: Pandoc
3.10.2, LibreOffice 26.2.5.2, Mermaid CLI 11.16.0, and Chromium 151.0.7922.173. The current final
image ships Mermaid CLI 11.17.0, so a document with a Mermaid diagram reports a Mermaid CLI pin
warning and `--require-pinned` refuses it until the recorded pin and the image are reconciled.

`doc.traceability.json` is canonical JSON without paths, timestamps, or document content. It
records the application and conversion-contract versions, the Pandoc reader, the source kind, size,
and SHA-256 digest, the template mode and digest, each required engine's approved and actual
version and status, whether every engine matched its pin, the requested outputs with sizes and
digests, and, for PDF, the service's PDF traceability manifest including page sizes and the font
manifest digest.

Reproducibility depends on the engines and fonts actually used. On a host, engine versions,
Fontconfig, and installed fonts can differ from the approved toolchain; local mode records engine
versions but cannot verify host fonts, and the font manifest digest identifies the configured
manifest only. Use the final image, preferably with `--require-pinned`, for results that match the
service.

### Timeouts and cancellation

The global `--timeout SECONDS` bounds the whole local conversion, including version checks; each
engine remains bounded by `MARKWEAVE_TEMPLATE_ENGINE_TIMEOUT_SECONDS`. An elapsed global timeout
fails with `conversion_timeout`. `SIGINT` (Ctrl-C) and `SIGTERM`, including `podman stop`, request
cooperative cancellation: the running engine's whole process group is terminated, its private
workspace is removed, and the command fails with `conversion_cancelled`. Expected failures exit with
status `1`; invalid or mixed options exit with status `2`.

### Run it with the bundled engines

The final image is the documented way to run local mode with the approved engines. Mount the
directory containing your files under `/work`, pass the example settings, and keep the hardened
runtime options of the [container deployment guide](container-deployment.md), including the
reviewed Chromium seccomp profile. With rootless Podman, `--userns=keep-id` keeps the outputs owned
by your user:

```bash
podman run --rm --userns=keep-id --user "$(id -u):0" \
  --read-only --cap-drop=all --security-opt=no-new-privileges \
  --security-opt=seccomp=toolchain/document-engines/chrome-seccomp.json \
  --network=none --memory=1g --pids-limit=256 \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777 \
  --tmpfs /work:rw,nosuid,nodev,size=256m,mode=0770 --shm-size=256m \
  --env-file examples/local-conversion.env \
  --volume "$PWD:/work/documents:Z" \
  ghcr.io/guillaume-lombardo/md-converter:VERSION@sha256:DIGEST \
  convert /work/documents/doc.md --local --to docx,pdf \
  --template /work/documents/template.docx --require-pinned -o /work/documents/out
```

Replace the image placeholder with the immutable published reference of a release that includes
local mode, as pinned in the quickstart Compose file. The output directory must already exist.
