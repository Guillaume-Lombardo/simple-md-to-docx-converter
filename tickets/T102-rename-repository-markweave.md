---
ticket: T102
linear_id: G1L-621
linear_url: https://linear.app/g1lom/issue/G1L-621/t102-rename-the-github-repository-to-markweave
status: Backlog
priority: Low
project: Markdown to DOCX and PDF Converter
---

# T102 - Rename the GitHub repository to markweave

## Objective

Align the repository name with the product name Markweave.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to change, item 7).

## Acceptance criteria

- Owner renames the GitHub repository; GitHub redirects remain in place.
- Update project URLs in `pyproject.toml`, README, docs, SECURITY/SUPPORT policies, OCI image labels, CLI help, quickstart clone commands and tests that assert URLs.
- Update the PyPI Trusted Publisher configuration and verify a dry-run publication path before the next release.
- Decide whether the GHCR image name `md-converter` is kept (no break) or migrated (requires a dual-publication and upgrade note).
- Rename the Linear project if the owner wants the names aligned.

## Dependencies

- None.

## Blocking points

- Owner-only actions: GitHub repository rename and PyPI Trusted Publisher update; publication fails until the publisher matches the new repository name.
- Do not schedule during a release-adoption window.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-621.
