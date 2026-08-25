# Agent workflow

Automated contributors follow the same review and quality rules as human contributors. The product
specification is normative; a ticket bounds the authorized change.

## Before editing

1. Read `AGENTS.md`, `docs/product-specification.md`, `README.md`, `CONTRIBUTING.md`, and the matching
   `tickets/Txx-*.md` file.
2. Fetch the Linear issue recorded by `linear_id` and synchronize it with the ticket by following
   `.agents/skills/sync-linear-tickets/SKILL.md`.
3. Inspect `git status --short --branch`. Preserve unrelated and user-owned changes.
4. Map the requested work to ticket dependencies and acceptance criteria. Do not choose values that
   section 14 leaves unresolved.
5. Use a short-lived Conventional Commit branch that does not contain an agent or automation name.

Keep repository artifacts in English. Use `uv` exclusively for Python environments and dependency
management. Make focused changes with tests for behavior, and do not refactor unrelated code.

## Verification

Run targeted tests first, followed by every applicable canonical check:

```bash
uv sync --all-groups
uv run ruff format .
uv run ruff check .
uv run ty check
uv run pytest -m "not requires_pandoc and not requires_mermaid and not requires_libreoffice"
uv run pytest
```

Use registered markers for unavailable external engines or services; do not add ad hoc skips. Report
every unavailable engine, skipped command, failed check, and unverified acceptance criterion. New
application behavior requires tests, at least 90% branch coverage, and at least 90% coverage of
changed Python lines. Boundary behavior requires the integration and final-image E2E coverage
defined by `AGENTS.md` and the specification.

## Tracking and handoff

Synchronize scope, status, acceptance criteria, dependencies, and progress with Linear and the local
ticket before and after implementation. Before requesting permission to push or publish a pull
request, present the branch, changed files, concise diff summary, checks run, and known limitations.
Pushing, opening or modifying a pull request, merging, force-pushing, and deleting a branch all
require explicit user approval. Never push directly to `main`.

At handoff, distinguish verified behavior from inference, identify operational decisions still
required, and link the exact files changed. Do not claim a skipped or unavailable check passed.
