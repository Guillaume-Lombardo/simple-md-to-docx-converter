# Local Mermaid rendering

T09 adds an internal preprocessing decorator between approved Markdown and the DOCX engine. It
does not add an API, queue, worker, or final application image; those workflows remain assigned to
later tickets.

## Supported Markdown

Fenced code blocks whose complete info string is `mermaid` are rendered in document order. Both
backtick and tilde fences are accepted, including fences inside supported CommonMark containers.
Additional fence options are not interpreted. Ordinary code, inline text, YAML scalars, and fences
for other languages remain unchanged.

Each diagram is replaced with one deterministic relative image reference under the reserved
`.md-converter-mermaid` directory beside the Markdown entrypoint. A collision with an input-package
resource fails closed. The original source does not reach Pandoc or appear in errors.

## Explicit limits

`MermaidLimits` provides no production defaults. It bounds diagram count, per-diagram and total
source bytes, both raw and normalized per-diagram and total rendered output bytes, and the maximum
displayed width and height. `ImageLimits` continues to bound decoded width, height, pixel count,
and normalized image structure. T18 owns the eventual production values.

Displayed dimensions use Pandoc's pixel unit at its fixed 96 DPI conversion. The preprocessor
chooses the limiting axis required by the configured width and height caps and emits only that
dimension so Pandoc preserves the decoded PNG ratio; document-provided dimensions are never
accepted for generated diagrams.

## Process and image boundary

`MermaidCliRenderer` creates a private workspace and invokes the pinned local `mmdc` executable
with fixed arguments, one diagram at a time. It writes fixed Mermaid `securityLevel: strict` and
Puppeteer configuration selecting the configured local Chrome executable with `headless: shell`.
Puppeteer downloads are disabled. No document content can change executable paths, CLI options,
theme, browser arguments, configuration, or output paths.

The subprocess has no shell or standard streams, receives an allowlisted deterministic environment,
and starts in its own process group. A timeout sends `SIGTERM` to the group and then `SIGKILL` after
the explicit grace period. Output and workspace failures use stable content-free error categories.

Chromium renders an untrusted PNG, which is decoded and normalized again by the T08 raster boundary.
This strips metadata, rejects animation or invalid content, and enforces decoded pixel limits before
the image is added to the immutable approved-resource manifest. The adapter opens the CLI output
once with no-follow and nonblocking semantics, validates that descriptor as a bounded regular file,
and never reopens the path. Pandoc revalidates the manifest before materialization.

## Runtime security and verification

The Python adapter does not claim to create network isolation. The runtime must deny network access,
keep Chrome's sandbox enabled, and provide the checksum-locked T00 seccomp profile. `--no-sandbox`
and equivalent relaxations are never used. T09 integration runs exercise the real Mermaid CLI
11.16.0, Chrome 151.0.7922.173, Pillow normalization, Pandoc, and OpenXML media path. The rootless
Podman proof uses an arbitrary UID, read-only root, no network, no capabilities, no-new-privileges,
bounded PID/memory/CPU/tmpfs resources, and the T00 profile. OpenShift proof remains deferred.

Final-image E2E remains assigned to T20/T21 because T09 delivers an internal synchronous component,
not a user-visible or operational workflow. This sequencing exception requires explicit reviewer
approval in the T09 pull request.
