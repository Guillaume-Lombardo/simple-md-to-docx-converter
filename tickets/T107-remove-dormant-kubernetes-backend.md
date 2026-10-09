---
ticket: T107
linear_id: G1L-627
linear_url: https://linear.app/g1lom/issue/G1L-627/t107-remove-the-dormant-kubernetes-isolation-backend-from-main
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T107 - Remove the dormant Kubernetes isolation backend from main

## Objective

Remove the roughly 6,500 lines of optional Kubernetes reverse-isolation code that serve the deferred T74 backlog ticket, keeping it recoverable from an archive tag.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to remove or freeze).

## Acceptance criteria

- Tag the last commit containing the code (for example `archive/t74-kubernetes-backend`) and reference the tag from T74.
- Remove `src/markweave/broker/kubernetes*`, `src/markweave/kubernetes_attester_process.py`, the CRI protobuf files, the `markweave-kubernetes-attester` entry point, the `kubernetes` optional extra, `containers/kubernetes-attester`, `deploy/k3s`, `deploy/reverse-kubernetes.yaml.example`, the Kubernetes E2E scripts, their tests, CI steps and `docs/kubernetes-reverse-isolation.md`.
- The rootless Podman broker backend, both transports, inventory, reconciliation and termination proof stay unchanged; reverse-conversion tests and both final-image E2E profiles pass.
- Record the removal of the `kubernetes` extra and entry point in CHANGELOG and the upgrade guide.
- Update specification sections 2 and 14 to say T74 would restore the backend from the archive tag.

## Dependencies

- None.
- Related: T74 - Design and implement the Kubernetes reverse-isolation backend

## Blocking points

- Owner confirms T74 stays deferred.
- Removing a published optional extra and console script is a packaging compatibility change; it must ship in a release with an upgrade note.
- If T96 is still open, T96 owns the specification wording for sections 2 and 14; only one ticket edits the specification at a time.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-627.
