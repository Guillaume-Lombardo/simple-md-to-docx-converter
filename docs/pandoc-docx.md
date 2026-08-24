# Pandoc DOCX conversion

T07 provides the internal synchronous Markdown-to-DOCX component. It accepts only Markdown that
passes pre-engine validation and invokes Pandoc with a fixed reader and fixed output arguments.
Queueing, APIs, workers, and final-container workflows are delivered by later tickets.

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
blocks.

This validation prevents Pandoc from fetching a remote destination supplied by a document. T07
does not claim operating-system network isolation; final runtime network policy belongs to the
container and deployment work. Pandoc's `--sandbox` option is not enabled because it also prevents
the approved local-resource behavior that T08 must implement and test.

## Process boundary

Each conversion uses a new temporary workspace containing only the Markdown input, opaque reference
DOCX, isolated home/cache/config/data/temp directories, and generated output. Pandoc receives no
shell, no standard input or captured document output, a process group of its own, and only the
allowlisted `LANG`, `LC_ALL`, `PATH`, and `TZ` host variables plus workspace-local directory
variables.

The arguments are fixed to the approved reader, DOCX writer, workspace reference document,
workspace resource path, and fixed input/output names. There are no user-controlled options,
filters, or include files. Conversion and termination-grace timeouts are required configuration;
T07 deliberately does not select the production values owned by T18. A timed-out process group is
terminated and then killed after its configured grace period.

## Stable failures

The component exposes content-free categories suitable for later API/job translation:

- `validation`: empty input, invalid YAML metadata, raw HTML, or forbidden resource destination;
- `workspace_failure`: workspace creation, preparation, output read, or cleanup failure;
- `pandoc_unavailable`: the configured executable cannot start;
- `pandoc_timeout`: conversion exceeds its configured deadline;
- `pandoc_failure`: Pandoc exits unsuccessfully;
- `invalid_docx`: successful execution returns an unsafe or structurally incomplete DOCX archive.

Errors do not include Markdown, template bytes, subprocess output, or workspace paths.

## Ownership boundaries

The reference DOCX is intentionally opaque to T07. T10 owns template validation, fonts, and style
policy. T08 owns archive/image security and approved local images, T09 owns Mermaid, T11 owns PDF,
and T18 owns production size/resource limits. The generated DOCX check here is therefore limited to
safe ZIP member names and the minimum required OpenXML parts; it is not a substitute for T10.

The real integration suite uses the exact approved Pandoc 3.10.2 artifact, converts the T04 corpus,
and inspects the resulting OpenXML for headings, lists, links, tables, footnotes, code styles,
attributes, Unicode text, and reference-document style propagation. CI downloads the official
release and verifies its locked SHA-256 before extraction. Final-image E2E is not applicable to this
internal component; the user-visible asynchronous conversion workflow and its rootless-image E2E
coverage belong to T20/T21.
