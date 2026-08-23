# Local development

## Bootstrap

Install `uv` using its supported installation method. From the repository root, synchronize every
dependency group:

```bash
uv sync --all-groups
```

The `.python-version` file requests Python 3.14. The `requires-python` constraint rejects a different
minor version, and `uv.lock` records the complete project and dependency-group resolution.

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

Pytest always measures the installed `md_converter` application package and enforces two separate
thresholds: at least 90% overall application coverage and at least 90% of application branches.
The branch-only Pytest hook validates Coverage.py JSON after every canonical run, so high line
coverage cannot hide low branch coverage. A complete, internally consistent report with zero
branches is defined as 100% branch coverage; missing, malformed, or contradictory branch totals
fail closed. Test doubles use the `mocker` fixture supplied by `pytest-mock`; importing
`unittest.mock` directly is rejected by Ruff and by the repository-local CI validator.

Ruff targets Python 3.14 and applies the committed correctness, security, modernization, import,
and maintainability rule families to source, CI helpers, and tests. Narrow per-file exceptions are
recorded only for reviewed subprocess argument vectors and for assertions and process probes in
tests. `ty` checks `src`, `scripts`, and `tests` explicitly against Python 3.14.

## Continuous integration

`.github/workflows/ci.yml` exposes the single stable required result `CI / gate`. It runs on pull
requests, merge-group candidates, pushes to `main`, published releases, manual dispatches, and a
provisional weekly schedule. Pull requests always run formatting, linting, type checking, unit
tests with blocking overall application coverage, an explicit branch-only JSON check, changed
application line coverage, lock validation, and cheap workflow security checks. Draft pull requests
do not run activated heavy domains.

For pull requests and merge-group candidates, CI writes `coverage.json` from the unit suite and
compares the reviewed base and head commits with `scripts/ci/check_changed_coverage.py`. At least
90% of changed executable lines under `src/md_converter` must be covered. The checker uses a
zero-context Git diff without shell interpretation, ignores tests and tooling, and validates every
Coverage.py file entry before measuring it. Executed, missing, and excluded line sets must contain
unique positive line numbers, be disjoint, and agree with statement summaries; branch arrays and
summaries must also be complete and internally consistent. A changed application file absent from
the report, an incomplete array, or an inconsistent count fails closed. A valid change containing
only excluded or non-executable lines remains 100% (`0/0`). Full history is checked out only where
commit comparison requires it.

Changed paths are mapped conservatively to functional, document-engine, storage-profile,
container, and E2E domains by `scripts/ci/select_domains.py`. Domain lifecycle metadata lives in
`.github/ci/domains.json`. A planned domain is reported in the Actions summary but is never counted
as executed. Activate a domain only in the ticket that delivers its real suite by changing its
status to `active` and adding the reviewed command as an argument array; the matrix runner invokes
that array without a shell. Scheduled, release, and manual runs select every domain, including both
storage profiles.

The `ci-infrastructure` domain is active now. Changes to the detector, runner, registry, workflow,
or its integration tests must run the real subprocess and quality-policy success and failure cases
in `tests/test_ci_runner.py` and `tests/integration/ci`; `CI / gate` rejects a skipped or failed
heavy matrix whenever any active domain was selected. Caches are restore-only outside trusted
pushes to `main`, including pull requests, forks, merge groups, releases, schedules, and manual
runs.

The Sunday 03:17 UTC schedule is provisional until T22 fixes the GitHub Actions usage budget and
final frequency. Release-triggered CI performs validation only; image and Python publication,
SBOM, provenance, and OIDC permissions remain isolated T22 work.

## Dependency changes

Use `uv add` to add a runtime dependency and `uv add --dev` to add a development dependency. Review
and commit both `pyproject.toml` and `uv.lock`. Do not edit resolved versions in `uv.lock` manually.

The PEP 517 backend is pinned in `build-system`, recorded in the `build` dependency group, and
constrained with its transitive dependencies in `tool.uv`. After changing any build dependency,
refresh the lock and regenerate the hash-checked build constraints:

```bash
uv lock
uv export --locked --only-group build --no-emit-project \
  --format requirements.txt --no-annotate --output-file build-constraints.txt
```

Build the source and wheel distributions in isolated build environments constrained by that file:

```bash
uv build --build-constraint build-constraints.txt --require-hashes
```

## External engines

No document engine is required for the current bootstrap tests. Follow the dedicated engine setup
documentation when those integrations are implemented; do not treat an unavailable engine test as
passed.
