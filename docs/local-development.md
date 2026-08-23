# Local development

## Bootstrap

Install `uv` using its supported installation method. From the repository root, synchronize every
dependency group:

```bash
uv sync --all-groups
```

The `.python-version` file selects Python 3.14. The `requires-python` constraint prevents accidental
use of a different minor version, and `uv.lock` records the complete resolution.

To reproduce the committed resolution without changing the lock file, run:

```bash
uv sync --locked --all-groups
```

## Development loop

Format and inspect the project before running tests:

```bash
uv run ruff format .
uv run ruff check .
uv run ty check
```

Run the default local suite, which excludes only tests requiring Pandoc, Mermaid/Chromium, or
LibreOffice:

```bash
uv run pytest -m "not requires_pandoc and not requires_mermaid and not requires_libreoffice"
```

Run the complete test collection when all required engines and services are available:

```bash
uv run pytest
```

Every external requirement must use its registered Pytest marker. PostgreSQL, S3, slow,
integration, and end-to-end tests remain included in the default command when they are runnable.

## Dependency changes

Use `uv add` to add a runtime dependency and `uv add --dev` to add a development dependency. Review
and commit both `pyproject.toml` and `uv.lock`. Do not edit resolved versions in `uv.lock` manually.

## External engines

No document engine is required for the current bootstrap tests. Follow the dedicated engine setup
documentation when those integrations are implemented; do not treat an unavailable engine test as
passed.
