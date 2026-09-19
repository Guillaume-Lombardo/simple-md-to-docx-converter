---
ticket: T78
linear_id: G1L-577
linear_url: https://linear.app/g1lom/issue/G1L-577/t78-make-node-asset-permission-tests-independent-of-checkout-modes
status: In Progress
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T78 - Make node asset permission tests independent of checkout modes

## Objective

Remove checkout-umask dependence from Kubernetes node asset tests while preserving secure installed permissions.

## Acceptance criteria

- Reproduce both test_kubernetes_node_assets.py failures when executable checkout files have mode 0775 instead of 0755.
- Test the Git executable contract separately from the exact installed permission contract.
- Use controlled fixtures for permissions and verify installation rejects or corrects unsafe modes without changing host permissions implicitly.
- Cover the deployment boundary with integration tests and the applicable final-image acceptance tests.
- Preserve wrapper/CNI contents and isolation policy and pass canonical checks.

## Dependencies

- T05
- T20

Coordinate node-asset ownership with T74; its pending live-cluster qualification
does not block this checkout-portability repair.

## Progress

- 2026-09-19: Created from the repository audit; this ticket does not authorize changes to a live cluster.

- 2026-09-19: Reproduced both exact-mode failures on an untouched 0775 checkout; Git records both assets as 100755. Implementation separates checkout executability from explicit secure installation in temporary directories. No live-cluster changes.

## Synchronization

Keep Linear and this mirror synchronized. Mark Done only after verification on main.
