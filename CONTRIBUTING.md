# Contributing

## Working agreement

Use English for repository artifacts. Keep each change mapped to one ticket and its acceptance
criteria. Read `AGENTS.md`, the product specification, and the relevant ticket before editing.

Development follows trunk-based delivery:

1. Synchronize the ticket with its Linear issue.
2. Create a short-lived `<type>/<issue>-<subject>` branch from `main`.
3. Keep the change focused and add tests for introduced behavior.
4. Run the canonical checks documented below.
5. Open a pull request to `main` and obtain independent review.
6. Squash merge only after required checks and discussions are complete.

Never push directly to `main`, force-push a shared branch, or resolve an unspecified product value
inside an implementation change.

## Environment

Install `uv`, then synchronize the Python 3.14 environment:

```bash
uv sync --all-groups
```

Do not use `pip`, Poetry, Pipenv, or a manually managed virtual environment. Add or remove Python
dependencies with `uv` and commit the resulting `uv.lock` update. When the build group changes,
regenerate `build-constraints.txt` using the command in the local development guide.

## Canonical checks

```bash
uv run ruff format .
uv run ruff check .
uv run ty check
uv run pytest -m "not requires_pandoc and not requires_mermaid and not requires_libreoffice"
uv run pytest
```

Use `uv run ruff format --check .` for a non-mutating formatting check. Tests that require a real
engine or service must use the marker defined in `pyproject.toml`; they must not hide unavailable
dependencies with ad hoc skips.

See [docs/local-development.md](docs/local-development.md) for the expanded workflow.
