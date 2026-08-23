# Markdown to DOCX and PDF Converter

This repository contains the foundation for a service that will convert Markdown documents to
DOCX and PDF. The product is under active development; conversion, HTTP, storage, and container
features are not implemented yet.

## Requirements

- [`uv`](https://docs.astral.sh/uv/)
- A platform supported by the Python 3.14 distribution managed by `uv`

`uv` reads `.python-version`, installs Python 3.14 when needed, and creates the project environment.
No manually managed virtual environment or direct `pip` invocation is required.

## Set up the project

```bash
git clone git@github.com:Guillaume-Lombardo/simple-md-to-docx-converter.git
cd simple-md-to-docx-converter
uv sync --all-groups
```

Run the canonical local checks:

```bash
uv run ruff format .
uv run ruff check .
uv run ty check
uv run pytest -m "not requires_pandoc and not requires_mermaid and not requires_libreoffice"
uv run pytest
```

These Pytest commands enforce at least 90% branch coverage for the `md_converter` application
package. Pull-request CI also enforces 90% coverage of changed executable application lines.
Tests use `pytest-mock`; direct imports from `unittest.mock` are rejected.

The lock file is committed. Use `uv sync --locked --all-groups` to require the committed dependency
resolution. Build dependencies are a locked project group and are also constrained explicitly for
isolated builds. Build distributions with the generated, hash-checked constraints:

```bash
uv build --build-constraint build-constraints.txt --require-hashes
```

## Repository map

- `src/md_converter/`: installable Python package
- `tests/`: automated tests
- `build-constraints.txt`: hash-checked constraints exported from the build dependency group
- `docs/architecture.md`: target architecture and component boundaries
- `docs/local-development.md`: detailed local workflow
- `tickets/`: repository-reviewed project ticket mirrors

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change. The normative product decisions
and delivery plan are in [docs/product-specification.md](docs/product-specification.md).
