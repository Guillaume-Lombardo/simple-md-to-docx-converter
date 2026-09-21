# T50 cross-surface qualification

This is the acceptance matrix for the package, CLI, HTTP boundary, configuration, containers,
recovery, documentation, and maintainability work delivered before T50. It does not choose a
release version or authorize publication.

T48's mutation-workflow integration is a separate follow-up. T50 verifies the reviewed mutation
domains, exclusions, and strict killed-only contract, but no actual mutation campaign ran at the
T50 candidate SHA. Reverse-conversion final-image success, extraction, security, and two-profile
acceptance belong to T73; T50 must not be described as completing that work.

## Reproducible commands

Run the local source checks from a clean candidate checkout:

```bash
uv sync --all-groups
uv run ruff format --check .
uv run ruff check .
uv run ty check
scripts/javascript/bootstrap-pnpm.sh "$PWD/.pnpm-tools"
export PATH="$PWD/.pnpm-tools/bin:$PATH"
export COREPACK_HOME="$PWD/.pnpm-tools/corepack-home" COREPACK_ENABLE_NETWORK=0
pnpm install --frozen-lockfile --ignore-scripts
pnpm run test:web
uv run python scripts/openapi_contract.py check
uv run pytest -m "not requires_pandoc and not requires_mermaid and not requires_libreoffice"
uv run pytest
uv run python scripts/ci/run_mutation_campaign.py \
  --mode all --artifact artifacts/t50/mutation-report.json
```

The first Pytest command is the canonical service-capable suite. It automatically provisions
loopback-only PostgreSQL and RustFS when external test services are absent, treats
`ResourceWarning` and unraisable-resource warnings as errors, and enforces the repository's 90%
application and branch coverage thresholds. The second command adds the real Pandoc,
Mermaid/Chromium, and LibreOffice tests. Missing engines are failures or explicitly reported
prerequisites, never passed rows.

The repository's existing `workflow_dispatch` path is the executable complete-suite coordinator.
After the exact candidate branch is pushed and will no longer change, dispatch it and bind the run
to the candidate SHA:

```bash
gh workflow run ci.yml --ref BRANCH
gh run list --workflow ci.yml --branch BRANCH --event workflow_dispatch \
  --json databaseId,headSha,status,conclusion,url
gh run watch RUN_ID --exit-status
```

Manual dispatch sets `COMPLETE_SUITE=true`; domain selection uses `--full`. It therefore runs every
registered domain rather than only paths changed by T50. Preserve the run URL, exact `headSha`,
selected-domain plan, job conclusions, and uploaded failure artifacts. A superseded SHA, skipped
required domain, or unsuccessful gate is not acceptance evidence.

Container qualification uses the existing rootless harnesses. Coordinate these CPU/memory-heavy
runs and retain their existing artifacts:

```bash
bash scripts/container/run-ci.sh
bash scripts/e2e/run.sh standalone
bash scripts/e2e/run.sh distributed
bash scripts/e2e/run-compose-all.sh
```

The final-image harness builds from the candidate by default. When qualifying already published
bytes, set both `MARKWEAVE_E2E_IMAGE` and `MARKWEAVE_E2E_FRONTEND_IMAGE` to the matched immutable
version-and-digest references from one publication receipt. T50 does not select those values and
must not compare current source claims with an older published image pair.

## Acceptance map

| Surface | Evidence |
| --- | --- |
| Package and optional dependencies | Clean sdist, wheel, editable, base, server, standalone, distributed, and complete install tests; public import and console entry point; retired-namespace rejection |
| CLI contracts | Root and family help, human and JSON output, stable exits, safe errors, and installed-shell tests for authentication, conversions/jobs, reversions, templates, administration/audit/health, runtime, and recovery |
| Authentication profiles | Non-echoing password input, no password/token persistence, atomic owner-only `0600` XDG files, TLS by default, redaction, symlink and permission rejection |
| Boundary discipline | Real-HTTP tests for business commands; runtime and recovery integration tests for the approved direct storage/process boundary |
| Accounts and authorization | Two regular users plus one administrator across CLI HTTP, final-image browser/API, ownership, audit, and restricted-session paths |
| Storage and recovery | Standalone SQLite/filesystem and distributed PostgreSQL/S3 contracts, production backup/restore, isolated restore, readiness, restart, cancellation, and failure recovery |
| Document engines | Real Pandoc DOCX, Mermaid/Chromium, LibreOffice PDF, reference corpus, and final-image workflows |
| HTTP and configuration | Canonical OpenAPI freshness/compatibility; complete `MARKWEAVE_*` reference; typed `MD_CONVERTER_*` equality and conflict behavior through 0.x |
| Maintainability | Resource warnings fail as tests, 90% total/branch/changed-line coverage, reviewed mutation domains with strict outcomes, clean `markweave` namespace, and local documentation links/fragments |
| Containers and parity | Rootless source build/smoke, installed CLI entrypoints, standalone/distributed final-image E2E, Docker/Podman Compose quickstarts, and failure artifacts |

The complete-suite run supplies exact-candidate hosted evidence for the unit/coverage, functional,
storage, document-engine, container, Compose, frontend, and both final-image E2E domains. The local
canonical suite additionally covers the clean source/sdist/wheel/editable namespace-install test,
the installed-shell/import tests, and the real-HTTP CLI integration modules; those integration
modules are not selected directly by a named complete-suite domain. The hosted final-image E2Es
independently exercise the public HTTP CLI boundaries in both storage profiles.

## Qualification record

Development checks on 2026-09-21 used T50 candidate
`3ee121a023b9207a72e2c18f0af9003bde69397e`, developed from
`4ef6a53f262a8ca33e34dce2a54a2c28933ed908`:

| Command or group | Result | Notes |
| --- | --- | --- |
| `uv sync --all-groups`; Ruff format/lint; `ty check` | Passed | Python 3.14.6 with the locked dependency graph. |
| Verified pnpm bootstrap; frozen install; `pnpm run test:web` | Passed | Exact repository toolchain and frontend coverage gates. |
| `uv run python scripts/openapi_contract.py check` | Passed | Canonical artifact is current. |
| Focused package, CLI, recovery, configuration, namespace, mutation-contract, quality, and documentation tests | Passed | 454 tests; this diagnostic selection used `--no-cov`, leaving coverage authority with the canonical suite. |
| Canonical engine-excluded Pytest suite | Passed | 4,400 passed, 56 deselected, 11 warnings, 94.95% coverage in 28m27.75s; automatic PostgreSQL and RustFS services were included. |
| [Complete hosted suite, attempt 2](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/actions/runs/35618280319/attempts/2) | Passed | Exact `headSha` `3ee121a023b9207a72e2c18f0af9003bde69397e`; `workflow_dispatch` selected every domain with `--full`. Light, both Python shards, coverage, functional, frontend, both storage profiles, document engines, CI infrastructure, container, Compose, both final-image E2Es, and the final gate passed. |
| Package and real-HTTP integration boundaries | Passed locally and in final-image workflows | The canonical suite ran the clean source/sdist/wheel/editable install, installed-shell/import, and real-HTTP CLI integration tests. The hosted unit shards verified package metadata and optional dependencies; the hosted container and E2E jobs exercised the installed CLI and public HTTP behavior. |
| Actual all-domain mutation campaign | Not run at the T50 SHA | Manifest/runner contracts passed. A separate T48 head `83ec1b44af15e18fbd1bf896204159208372b106` killed 25/25 selected mutants with zero strict failures and a passing CI mutation job; that result is not exact-head T50 evidence. |
| Source containers, final-image profiles, recovery, and quickstarts | Passed in the complete hosted suite | The container job exercised production standalone and distributed backup/restore. Both E2Es covered two regular users plus one administrator, installed CLI families, restarts, readiness outages, checkpoint restore, and failure recovery. No older published image pair was substituted. |
| Independent review | Approved | The orchestrator independently reviewed the scoped evidence/navigation change and reported no blocking issue. |

This evidence qualifies the candidate commit. T50 remains In Progress until the integrated change
is merged and verified on `main`.

## Residual limitations

- T48 owns its remaining repository workflow integration. T50 qualifies the delivered mutation
  contract without claiming an exact-head campaign or that follow-up complete. T48 remains
  nonblocking for T50.
- The UBI package repositories moved after this candidate's successful hosted run. A later
  source-image build now detects six reviewed curl/OpenSSL package updates against the committed
  inventory. T86 PR #255 owns the user-approved inventory correction; T48's otherwise-passing CI
  observes the same RPM mismatch in its E2E build. This cross-cutting drift does not turn T48 into a
  T50 dependency, and the next integrated-main verification must use the corrected inventory.
- T73 owns successful reverse extraction against exact final images, reverse-specific security,
  cancellation/recovery, and both-profile acceptance.
- OpenShift target-cluster proof remains deferred by the product specification. No T50 result may
  claim OpenShift qualification.
- Selecting or publishing a new version requires a separate product-manager decision and release
  workflow.

Continue through the [documentation index](../index.md), [CLI guide](../cli.md),
[Python distribution guide](../python-distribution.md),
[container deployment](../container-deployment.md), [configuration reference](../configuration.md),
[recovery guide](../recovery.md), [upgrade guide](../upgrading.md),
[release process](../releasing.md), [changelog](../../CHANGELOG.md),
[security policy](../../SECURITY.md), and [support policy](../../SUPPORT.md).
