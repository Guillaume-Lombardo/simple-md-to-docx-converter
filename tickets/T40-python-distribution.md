---
ticket: T40
linear_id: G1L-418
linear_url: https://linear.app/g1lom/issue/G1L-418/t40-clarify-and-optimize-the-python-distribution
status: In Progress
priority: High
project: Markdown to DOCX and PDF Converter
---

# T40 - Clarify and optimize the Python distribution

## Objective

Make the PyPI distribution a coherent supported deployment surface with clear metadata, dependency extras, and container installation behavior.

## Acceptance criteria

* Define the supported Python API as exactly `import markweave` and `markweave.__version__`; the installed CLI is the supported programmatic operation surface, all other modules remain internal until separately documented, and the final container remains the recommended production deployment.
* Make the base install the remote HTTP CLI and shared types only; define exact `server`, `standalone`, `distributed`, and `all` extras, where `server` adds common API/worker dependencies, each profile extra includes `server` plus only its SQLite/filesystem or PostgreSQL/S3 dependencies, and `all` is the union used by the final image.
* Ensure missing optional dependencies produce precise startup/command errors only when their feature is selected.
* Add maintainers/authors, classifiers, keywords, support and documentation URLs, and complete Apache-2.0 metadata to PyPI artifacts.
* Verify sdist/wheel contents, clean Python 3.14 installation for each supported extra, CLI availability, import isolation, and container dependency completeness.

## Dependencies

* T31
* T39
* T22

## Implementation boundary

* Exclusively own `pyproject.toml`, dependency groups/extras, package metadata, `scripts/release/verify_install.py`, its tests, and PyPI-facing documentation after T31 merges.
* Treat CLI and configuration behavior as upstream contracts; do not implement feature commands.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.
* 2026-08-29: Audit follow-up fixed the public Python surface, exact extras matrix, and exclusive distribution-file ownership.
* 2026-08-30: Implementation started on `feat/T40-python-distribution` from `d3c7a2f`. This work owns package metadata, optional dependency groups, release-install verification, and narrowly scoped PyPI distribution documentation.
* 2026-08-30: Implemented the public `markweave.__version__` surface, base/server/profile/all extras, PyPI metadata, clean-install verification, and final-image `all` selection. The real release-artifact test verified all five clean installation profiles. A minimal deferred-import integration remains after T44 releases `app.py`: it must load S3 dependencies only for the distributed profile so `server` stays S3-free and selected missing backends produce feature-local guidance.
* 2026-08-30: Integrated T44 from `main` and completed the deferred backend boundary. Server and standalone imports no longer load boto3 or botocore, distributed startup loads PostgreSQL and S3 clients only after profile selection, and missing backend extras produce precise `markweave[distributed]` guidance. The clean-artifact integration test passed for all five installation profiles; 1,545 unit tests passed with 93.72% total application coverage. The canonical non-engine command additionally passed 1,757 tests but could not run 31 PostgreSQL/RustFS boundary tests because the local service environment variables were unavailable.
* 2026-08-30: PR #115 final-image validation found that the container still resolved the removed root-level `markweave:create_app` export. The entrypoint and authentication guide now use the internal runtime path `markweave.app:create_app`, while package and clean-install regressions keep `create_app` absent from the supported root API and verify that server installations retain the callable internal factory. The focused package, release, documentation, and container suite passed 89 tests with `--no-cov`, and all 1,546 unit tests passed with 93.72% application coverage.

## Coordination

* Status: In Progress.
* One worker owns this ticket's implementation files at a time.
* T44 released `app.py` and storage lifecycle ownership on `main`; T40 preserves its resource cleanup while limiting its edits to optional backend loading.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
