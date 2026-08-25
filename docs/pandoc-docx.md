# Pandoc DOCX conversion

The worker's synchronous Markdown-to-DOCX component accepts only Markdown that passes pre-engine
validation and invokes Pandoc with a fixed reader and fixed output arguments.

## Accepted dialect and validation

The reader is fixed to:

```text
commonmark_x+pipe_tables+footnotes+attributes+yaml_metadata_block-raw_html
```

Before Pandoc starts, the service parses CommonMark together with front-matter and footnote
extensions. YAML is composed with a safe, non-object-constructing loader and its decoded scalar
nodes are traversed once, so quoted escapes and aliases cannot bypass the checks. Invalid YAML is
rejected. The validator rejects raw HTML and every link or image destination that uses a URI scheme,
protocol-relative form, or encoded equivalent. The same conservative checks apply inside YAML
front matter and footnote continuations, where extension syntax could otherwise hide content from
a plain CommonMark parse. Literal HTML and URLs remain permitted inside actual code spans and code
blocks. Standalone Markdown still rejects every image token. Archive inputs may bind a safe
relative image destination to a normalized PNG in an immutable approved-resource manifest; missing,
escaping, ambiguous, absolute, and remote destinations fail before the engine. Pandoc raw-code
attributes such as `{=html}` and `{=tex}` are also rejected when attached to inline code or used as
a fence info string. The same characters remain valid as literal code content.

This validation prevents Pandoc from fetching a remote destination supplied by a document. The
adapter does not claim operating-system network isolation; deployment network policy supplies that
boundary. Pandoc's `--sandbox` option is not enabled because it also prevents the approved local
resource behavior.

## Process boundary

Each conversion uses a new temporary workspace containing only the Markdown input, approved
normalized resources, generated normalized Mermaid PNG resources, opaque reference DOCX,
isolated home/cache/config/data/temp directories, and generated output. Pandoc receives no shell,
no standard input or captured document output, a
process group of its own, and only the explicitly supplied `PATH`, fixed `LANG=C.UTF-8`,
`LC_ALL=C.UTF-8`, and `TZ=UTC`, plus workspace-local directory variables. Host locale, timezone,
and unrelated environment values are not inherited.

The arguments are fixed to the approved reader, DOCX writer, workspace reference document,
workspace resource path, and fixed input/output names. There are no user-controlled options,
filters, or include files. Conversion and termination-grace timeouts are required configuration;
the adapter does not select production values. A timed-out process group is
terminated and then killed after its configured grace period.

## Stable failures

The component exposes content-free categories suitable for later API/job translation:

- `validation`: empty input, invalid YAML metadata, raw HTML, a Pandoc raw-code attribute, forbidden
  resource destination, or an image not materialized and approved by the input-package boundary;
- `workspace_failure`: workspace creation, preparation, output read, or cleanup failure;
- `pandoc_unavailable`: the configured executable cannot start;
- `pandoc_timeout`: conversion exceeds its configured deadline;
- `pandoc_failure`: Pandoc exits unsuccessfully;
- `invalid_docx`: successful execution returns an unsafe or structurally incomplete DOCX archive.

Errors do not include Markdown, template bytes, subprocess output, or workspace paths.

## Ownership boundaries

The reference DOCX is intentionally opaque to this adapter. Template validation owns fonts and
style policy. The archive/image, Mermaid, PDF, and resource-policy boundaries supply the validated inputs
and production limits. The generated DOCX check here is therefore limited to
safe ZIP member names and the minimum required OpenXML parts; it is not a substitute for template
validation.

The real integration suite uses the exact approved Pandoc 3.10.2 artifact, converts the T04 corpus,
and inspects the resulting OpenXML for headings, lists, links, tables, footnotes, code styles,
attributes, Unicode text, Mermaid media, and reference-document style propagation. CI downloads the
official Pandoc and Chrome artifacts, verifies their locked SHA-256 values, and installs Mermaid
from the T00 lock with Puppeteer downloads disabled. Final-image E2E additionally covers the
user-visible asynchronous conversion workflow in both storage profiles.
