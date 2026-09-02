# Local development

Browser development now lives under `web/`; FastAPI serves only API, download, OpenAPI, health,
readiness, and metrics routes. Run the frontend quality gates from `web/` and the root Python gates
with `uv`. Production-bound testing must exercise the separate rootless frontend and backend images
through `web/router.mjs`; the development server is not production evidence.

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

## Run a complete local service

Use the root Compose evaluation profile for the maintained, complete local service. It supplies all
required application settings, the released rootless image, real ClamAV, persistent standalone
storage, bounded writable mounts, and loopback-only HTTP. Follow the password creation, health wait,
first conversion, and non-destructive shutdown steps in the [README quickstart](../README.md#try-it-locally).
That profile intentionally runs the released image rather than the current worktree.

To run worktree source instead, install every locked document engine and provide ClamAV plus every
required setting in the [configuration reference](configuration.md). There is intentionally no
partial development default set: `Settings.load()` validates the complete profile before serving
and reports invalid assembly without echoing field values. After supplying the full environment,
start the standalone API and embedded worker with:

```bash
export MARKWEAVE_HOST=127.0.0.1
export MARKWEAVE_PORT=8000
uv run markweave serve
```

The package runtime binds `MARKWEAVE_HOST` and `MARKWEAVE_PORT`, documented with the other
runtime variables. For authenticated browser use, terminate TLS locally and set
`MARKWEAVE_PUBLIC_ORIGIN` to the exact browser-visible HTTPS origin, including a non-default port.
That configured value, rather than proxy forwarding headers, is authoritative for login Origin
validation. Keep the application listener private to the terminator and do not enable broad proxy
header trust. Direct or end-to-end TLS may omit the setting only when the ASGI-visible scheme, host,
and port already equal the browser origin.

Do not commit bootstrap credentials or place production secrets in shell history. The request-body
limit must exceed the source limit, and the worker heartbeat must remain shorter than its lease;
all other cross-field rules are listed in the configuration reference.

For disposable local development, point `MARKWEAVE_STANDALONE_DATA_DIRECTORY` at a writable
directory rather than production `/data`. Distributed development requires a real PostgreSQL
database and an AWS S3-compatible bucket. See [storage-profiles.md](storage-profiles.md) for the
complete configuration and recovery contract.

## Development loop

Format and inspect the project before running tests:

```bash
uv run ruff format .
uv run ruff check .
uv run ty check
```

Verify the native browser module with the locked, dependency-free Node test package. Its command
blocks below 90% line, branch, or function coverage:

```bash
npm ci --ignore-scripts
npm run test:web
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
integration, and end-to-end tests remain included in the default command. Configure
`MARKWEAVE_TEST_POSTGRES_URL` and the `MARKWEAVE_TEST_S3_*` variables before the canonical
run. The distributed CI domain provisions PostgreSQL and RustFS and never substitutes MinIO.

Pytest always measures the installed `markweave` application package and enforces two separate
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
provisional weekly schedule. Pull requests always run formatting, linting, type checking, native
browser-module tests with independent coverage gates, unit tests with blocking overall application
coverage, an explicit branch-only JSON check, changed application line coverage, lock validation,
and cheap workflow security checks. Draft pull requests do not run activated heavy domains.

T48 phase 1 defines the reviewed, risk-ranked mutation manifest, runner, evidence format, and
mutation-driven tests before activating the expanded gate. Its five bounded domains cover
observability, authentication/session, archive/SVG, job integrity, and retention/storage. The
observability domain preserves the original
`markweave.observability.x__normalize_method__mutmut_*` target and
`tests/unit/test_observability.py`. Its JSON evidence records the exact selected and killed counts;
the phase-2 gate rejects every surviving, untested, suspicious, timed-out, interrupted, or
crashing mutant. Phase 2 activates it only from a trusted `main` base revision and immediately
restores `Mutation / critical gate` as a required check.

For pull requests and merge-group candidates, CI writes `coverage.json` from the unit suite and
compares the reviewed base and head commits with `scripts/ci/check_changed_coverage.py`. At least
90% of changed executable lines under `src/markweave` must be covered. The checker uses a
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

Active domains run their registered functional, document-engine, storage, container, and final-image
suites as selected. The stable gate fails when a selected active domain is skipped or fails.
Release publication, SBOM attachment, provenance, and OIDC permissions are isolated in the release
workflow described in [releasing.md](releasing.md).

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

The default local suite excludes tests marked for Pandoc, Mermaid/Chromium, and LibreOffice. Install
the locked engines to run their integration suites and the complete final-image tests. An unavailable
engine test is skipped only through its registered marker and must never be reported as passed.
