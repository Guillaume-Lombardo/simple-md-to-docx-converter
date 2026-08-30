# Python distribution

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

Select a server or storage profile only when operating a local service. Missing optional backend
dependencies must be reported by that selected feature with installation guidance; importing
`markweave` and running the remote CLI do not require them.

## Release verification

Release automation builds one sdist and wheel from the reviewed commit. It verifies artifact
contents and integrity, installs the wheel in clean Python 3.14 environments for the base package
and every supported extra, checks the public import and console command, and verifies that the
final-image installation selects the `all` union. See [the release process](releasing.md) for the
publication and evidence requirements.
