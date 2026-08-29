---
ticket: T40
linear_id: G1L-418
linear_url: https://linear.app/g1lom/issue/G1L-418/t40-clarify-and-optimize-the-python-distribution
status: Backlog
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

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.
