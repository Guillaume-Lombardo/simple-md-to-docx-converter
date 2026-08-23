# Repository Instructions

## Authority and scope

- Read `docs/product-specification.md` before making product, architecture, security, deployment, or acceptance-test decisions.
- The product specification is the sole normative source for those decisions. This file contains only operational repository rules. If the two diverge, the product specification prevails.
- Treat the specification as context, not as authorization to implement work outside the current request.
- Map every change to an explicit objective and the relevant T00–T23 tickets and acceptance criteria.
- Do not invent values listed as unresolved in section 19. Keep them configurable when a provisional value is harmless; otherwise request a decision before changing a contract, architecture, or acceptance test.

## Language

- Use English for every repository artifact: source code, identifiers, comments, docstrings, tests, fixtures, API error messages, user-interface copy, logs, documentation, commit messages, branch subjects, and pull-request content.
- Translate modified legacy French text to English in the same change. Proper names and externally defined values are exempt.

## Required toolchain

- Target Python 3.14.
- Use `uv` exclusively for Python versions, virtual environments, dependency locking, and command execution. Do not use `pip`, Poetry, Pipenv, or a manually managed virtual environment.
- Use Ruff for formatting and linting, `ty` for static type checking, Pytest for all Python tests, pytest-cov for coverage, and `pytest-mock` for test doubles.
- Do not import `unittest.mock` directly.
- Use these canonical commands once the project configuration exists:

  ```bash
  uv sync --all-groups
  uv run ruff format .
  uv run ruff check .
  uv run ty check
  uv run pytest -m "not requires_pandoc and not requires_mermaid and not requires_libreoffice"
  uv run pytest
  ```

- Use `uv run ruff format --check .` in CI or whenever a non-mutating formatting check is required.
- Do not invent substitute commands. If the repository cannot yet execute a canonical command, report that bootstrap gap explicitly.

## Development workflow

1. Read this file, the product specification, the README, and any applicable contribution instructions.
2. Inspect `git status --short --branch` and preserve unrelated work.
3. Identify the relevant ticket dependencies and acceptance criteria before editing.
4. Keep changes scoped and preserve the architecture and tools selected by the specification.
5. Add or update tests for every behavioral change.
6. Run targeted checks first, then every applicable canonical check.
7. Report every skipped command, unavailable dependency, failed check, and unverified acceptance criterion.

## Tests and external engines

- Register and use the markers defined by the product specification, including `unit`, `functional`, `integration`, `e2e`, `slow`, `requires_pandoc`, `requires_mermaid`, `requires_libreoffice`, `requires_postgres`, and `requires_s3`.
- The default local test command excludes only Pandoc, Mermaid/Chromium, and LibreOffice tests, as shown above. It must not silently exclude PostgreSQL, S3, slow, integration, or E2E tests when those suites are otherwise runnable.
- Tests requiring an unavailable engine must carry the matching marker. Never skip them through ad hoc environment checks or an unconditional `pytest.skip`.
- Run the full suite when all required engines and services are available. Report missing engines rather than treating their tests as passed.
- Unit tests must not use the network, containers, document engines, or real external databases.
- Maintain at least 90% branch coverage for application Python code and 90% coverage of changed Python lines.
- Use security tests for archive paths, SVG, uploads, subprocesses, authentication, authorization, queue leases, idempotency, and concurrency whenever those areas change.
- Inspect DOCX output as OpenXML archives and use the reference corpus and golden tests for rendering changes.

## Git and review

- Follow trunk-based development: `main` is the only long-lived branch; all work uses a short-lived branch and a pull request.
- Never push directly to `main`.
- Branch names must never contain `codex`, an agent name, or an automation-tool name.
- Name every branch with a short Conventional Commit type and subject, such as `feat/<issue>-<subject>`, `fix/<issue>-<subject>`, `docs/<issue>-<subject>`, or `chore/<issue>-<subject>`; use `chore`, never `chores`.
- Keep pull requests cohesive and reviewable. Use squash merge only after required checks and an independent review.
- Before any push or pull-request publication, present the branch, changed files, concise diff summary, checks run, and known limitations, then obtain explicit approval.
- Never force-push, merge, delete a branch, or bypass a protection without explicit approval.

## Agent coordination

- Use multiple agents only when the user or active operating mode explicitly authorizes it and task boundaries are stable.
- Assign a file or component to only one implementation agent at a time.
- Keep implementation and review roles independent. An implementation agent must not approve or merge its own pull request.

## Local notes

- `.agent/local-environment.md` is machine-specific and ignored by Git. Keep it in English and store only stable, non-sensitive environment notes.
- Never store secrets, tokens, credentials, or document data in local notes.
