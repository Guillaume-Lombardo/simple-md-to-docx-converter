---
ticket: T28
linear_id: G1L-390
linear_url: https://linear.app/g1lom/issue/G1L-390/
status: Done
priority: High
project: Markdown to DOCX and PDF Converter
---

# T28 - Add an insecure SSH-tunnel quickstart and publish 0.3.5

## Objective

Provide an explicit temporary evaluation mode that omits ClamAV and login-origin validation for
testing through an SSH tunnel, while preserving secure defaults and fixing native browser login in
the normal mode.

## Acceptance criteria

- Login-origin validation remains enabled by default and continues to reject mismatched origins.
- The secure login page uses a referrer policy that preserves the same-origin browser form origin.
- `scripts/quickstart-simple.sh up --insecure` omits ClamAV and disables login-origin validation.
- The insecure quickstart accepts `Origin: null` and arbitrary origins far enough to return the
  invalid-credential response, and reports readiness only after verifying that behavior.
- The insecure quickstart remains published only on `127.0.0.1` for SSH-tunnel access.
- Startup emits unmistakable warnings that upload scanning and login-origin protection are
  disabled and the mode must never be network-exposed or used in production.
- Unit, integration, Compose-contract, and final-rootless-image E2E tests cover the secure default
  and explicit insecure exception.
- User and operator documentation describes the bounded temporary-testing use case.
- The verified package and container are published as patch release `0.3.5`, then the quickstart is
  repinned to that immutable image digest in a follow-up change.

## Dependencies

- T24
- T26
- T27

## Progress

- 2026-08-28: Started from verified `main` after a tunneled browser login exposed `Origin: null`.
  Diagnosis found that Markweave's `Referrer-Policy: no-referrer` caused native same-origin POST
  forms to serialize a null origin, while explicit-origin probes proved Compose propagation was
  correct. Scope includes the secure policy correction and a separate, explicit loopback-only
  insecure mode for temporary SSH-tunnel testing.
- 2026-08-28: Added the fail-closed `insecure_evaluation_mode` setting, explicit simple-quickstart
  flag, ClamAV-free topology reuse, null/arbitrary-origin readiness probes, structured and terminal
  warnings, loopback-only final-image E2E coverage, the secure `same-origin` referrer-policy fix,
  documentation, and the `0.3.5` patch bump. Ruff, `ty`, Bash syntax, ShellCheck for the changed
  quickstart, 23 JavaScript tests, 233 focused tests, and 1,358 unit tests at 93.64% coverage pass.
  The canonical service-inclusive suite reached 1,555 passing tests at 95.05% coverage but remains
  unavailable locally because PostgreSQL/RustFS settings are absent. The pinned-browser test is
  prepared to exercise the native form without an injected Origin but cannot run locally because
  the reviewed Chrome executable is not installed; CI owns that execution.
- 2026-08-28: Pull request #96 passed its complete ready-PR matrix and independent review, then was
  squash-merged to `main` at `3ac1066bcf1898e99cc860ce262d79b52e43a62b`. Automatic release run
  33176613371 published final GitHub Release and tag `v0.3.5`, the PyPI wheel and source
  distribution, and the attested GHCR image with SBOM and release evidence. The quickstart now
  pins that anonymously readable image at registry digest
  `sha256:2697d86ffddf51040d633614b66258e65308b94305cbef708d2631be9b9156d4`.

## Synchronization

Update this file and Linear whenever scope, status, priority, dependencies, acceptance criteria,
or progress changes.
