---
name: develop-markdown-converter
description: Design, implement, test, or document this repository's asynchronous Markdown-to-DOCX/PDF conversion service according to its product specification.
---

# Develop the Markdown converter

## Establish scope

1. Read `AGENTS.md` and `docs/product-specification.md` in full before proposing architecture or modifying code.
2. Identify the relevant T00–T23 tickets, acceptance criteria, and dependencies.
3. Distinguish decisions fixed in section 2 from parameters to determine in section 19. Do not silently choose limits, retention, PDF/A, table-of-contents behavior, sandboxing, fonts, or operational settings.
4. Keep the change within the requested outcome even when the specification describes later work.

## Design the change

- Preserve the target pipeline: validation and extraction, local Mermaid rendering and image normalization, Pandoc to DOCX, then LibreOffice headless to PDF.
- Preserve asynchronous conversion, the brokerless persistent queue, and the common contract shared by the `standalone` SQLite/PVC and `distributed` PostgreSQL/S3 profiles.
- Isolate Pandoc, Mermaid, and LibreOffice adapters from domain logic so tests remain deterministic.
- Model errors with stable categories and precise English messages without placing document content in logs.
- Apply the security invariants in the product specification to uploads, ZIP files, paths, SVG, subprocesses, temporary files, and remote resources.
- Preserve template identity and ownership during rename or replacement; create atomic versions, restore by copy-forward, and enforce `ETag`/`If-Match`.
- Freeze the template and version when creating a job; preserve the state machine, idempotency, leases, cancellation, expiration, and cleanup semantics.

## Implement and verify

1. Use Python 3.14, `uv`, Ruff, `ty`, Pytest, pytest-cov, and pytest-mock through the canonical commands in `AGENTS.md`.
2. Write acceptance-focused tests first when they clarify the contract.
3. Cover the happy path, configured limits, external-engine failures, and applicable security scenarios.
4. Test ownership and administration with two users and one administrator for every affected route.
5. Maintain the 90% application and changed-line coverage thresholds, then run targeted checks and every applicable canonical check.
6. Review the diff against ticket criteria and cross-cutting security, observability, rootless execution, and English-language requirements.

## Document the result

- Update OpenAPI and examples when the HTTP contract changes.
- Update user, administrator, or operations guides when visible or operational behavior changes.
- Record temporary assumptions as configuration or known limitations, never as final decisions.
- Report covered criteria, executed checks, and remaining validation gaps.
