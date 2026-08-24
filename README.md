# Markdown to DOCX and PDF Converter

This repository contains the foundation for a service that will convert Markdown documents to
DOCX and PDF. It currently provides local authentication, administrative account management,
revocable sessions, health endpoints, and durable standalone or distributed storage profiles.
It also provides template ownership, visibility-aware search, preference, and fallback persistence
foundations. The internal conversion component now validates standalone Markdown or bounded ZIP
packages, binds and normalizes approved local images, sanitizes and rasterizes untrusted SVG, and
runs Pandoc in an isolated workspace. Queue, worker, versioned template APIs, UI, and final-container
features remain under development.

## Requirements

- [`uv`](https://docs.astral.sh/uv/)
- A platform supported by the Python 3.14 distribution managed by `uv`
- Pandoc 3.10.2 for real DOCX conversion and the Cairo shared library for SVG rasterization; the
  validated T00 UBI toolchain image provides both

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

These Pytest commands independently enforce at least 90% overall coverage and 90% branch-only
coverage for the `md_converter` application package. Pull-request CI also enforces 90% coverage of
changed executable application lines. Tests use `pytest-mock`; direct imports from `unittest.mock`
are rejected.

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
- `docs/authentication.md`: local accounts, sessions, configuration, and current limitations
- `docs/storage-profiles.md`: profile configuration, backup, and restore procedures
- `docs/templates.md`: template identity, visibility, selection, and T15 boundaries
- `docs/golden-testing.md`: reference corpus and deterministic DOCX/PDF comparison helpers
- `docs/pandoc-docx.md`: approved Markdown dialect and isolated Pandoc DOCX boundary
- `docs/archive-images.md`: secure archive and local-image preparation contract
- `docs/local-development.md`: detailed local workflow
- `tickets/`: repository-reviewed project ticket mirrors

See [CONTRIBUTING.md](CONTRIBUTING.md) before proposing a change. The normative product decisions
and delivery plan are in [docs/product-specification.md](docs/product-specification.md).
