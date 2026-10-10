# Python distribution

Markweave supports Markdown to DOCX, PDF and editable PowerPoint, plus experimental document-to-
Markdown conversion. See the [capability matrix](index.md#conversion-capabilities) for formats,
limits and deployment requirements. The remote CLI uses an existing service; reverse conversion
requires that service's separately configured [external broker](reverse-broker-deployment.md).

`markweave` is the PyPI package and public Python import. Its supported Python API is deliberately
small:

```python
import markweave

print(markweave.__version__)
```

All other Python modules are internal implementation details. Automations should use the installed
`markweave` command-line interface for supported remote user and administrator operations. The
rootless Markweave container remains the recommended production deployment.

## Installation profiles

The base package installs the remote HTTP CLI and its shared standard-library types. It does not
install server, database, object-store, document-processing, or web dependencies.

| Installation | Use |
| --- | --- |
| `markweave` | Remote HTTP CLI only. |
| `markweave[packaging]` | Remote CLI plus the Markdown parser libraries that let `markweave convert` package referenced local images. |
| `markweave[server]` | Common API, worker, document-processing, and SQL dependencies. |
| `markweave[standalone]` | `server` plus the standard-library SQLite and filesystem profile. |
| `markweave[distributed]` | `server` plus PostgreSQL and S3-compatible storage dependencies. |
| `markweave[all]` | Union used by the final Markweave container. |

For example:

```bash
uv tool install 'markweave'
markweave --help

uv tool install 'markweave[distributed]'
```

The `packaging` extra installs only `markdown-it-py`, `mdit-py-plugins`, and `pyyaml`, with the
same constraints as `server`. With it, `markweave convert` finds and resolves Markdown image
references with `markweave.conversion.image_references`, the module the service itself uses
(owner decision A2 for T104, 2026-10-10). That module imports no image engine, database, or HTTP
server dependency. Without the extra, a Markdown source containing image syntax fails before upload
with installation guidance (`packaging_unavailable`) unless `--no-package` is passed; Markdown
without image syntax is uploaded unchanged as before. The `server`, `standalone`, `distributed`,
and `all` profiles, and therefore the final image, already include these libraries.

Select a server or storage profile only when operating a local service. Missing optional backend
dependencies must be reported by that selected feature with installation guidance; importing
`markweave` and running the remote CLI do not require them.

## Release verification

Release automation builds one sdist and wheel from the reviewed commit. It verifies artifact
contents and integrity, installs the wheel in clean Python 3.14 environments for the base package
and every supported extra, checks the public import and console command, and verifies that the
final-image installation selects the `all` union. See [the release process](releasing.md) for the
publication and evidence requirements.

The experimental reverse-conversion attempt is intentionally outside that union. Its disposable
image installs the `reverse-attempt` extra, which pins `firecrawl-anydoc==0.2.4` and the bounded T08
image-normalization dependencies. The ordinary server, standalone, distributed, and `all` extras
do not install anydoc. See [the reverse-attempt image contract](reverse-attempt-image.md).
