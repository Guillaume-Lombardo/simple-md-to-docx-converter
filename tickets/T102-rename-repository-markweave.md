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
- Before the rename, add the new repository name as a second PyPI Trusted Publisher; remove the old publisher only after a successful release from the renamed repository.
- Keep the GHCR image name `md-converter` (owner decision 2026-10-09); no image migration.
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
