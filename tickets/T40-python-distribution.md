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

* Define and document the supported public Python API and state clearly when the container remains the recommended deployment.
* Split backend-only dependencies into reviewed extras such as standalone, distributed, and all/full without breaking runtime imports or the final image.
* Ensure missing optional dependencies produce precise startup/command errors only when their feature is selected.
* Add maintainers/authors, classifiers, keywords, support and documentation URLs, and complete Apache-2.0 metadata to PyPI artifacts.
* Verify sdist/wheel contents, clean Python 3.14 installation for each supported extra, CLI availability, import isolation, and container dependency completeness.

## Dependencies

* T31
* T39
* T22

## Implementation boundary

* Own `pyproject.toml`, dependency groups/extras, package metadata, release-install verification, and PyPI-facing documentation.
* Treat CLI and configuration behavior as upstream contracts; do not implement feature commands.

## Progress

* 2026-08-29: Created from the approved package review. The product manager approved the complete CLI surface, HTTP-only business commands, direct operational commands, XDG `0600` session profiles without API tokens, and `MARKWEAVE_*` migration with `MD_CONVERTER_*` compatibility through 0.x.

## Coordination

* Status: Backlog.
* One worker owns this ticket's implementation files at a time.
* Synchronize Linear and the repository mirror before starting and after every scope, dependency, status, or progress change.
* All repository artifacts and user-facing text are English.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria, implementation boundaries, or progress changes.

