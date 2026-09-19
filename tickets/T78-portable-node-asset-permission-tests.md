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

- 2026-09-20: Implementation PR [#238](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/238) separates owner executability and Git index mode 100755 from real installed mode 0755. Twenty deployment integration cases exercise documented install commands with controlled source modes, umasks, unsafe destination replacement, and missing-directory failure; the wrapper/CNI contents and isolation policy are unchanged.
- Validation: 23 focused tests passed; `uv sync --all-groups`, Ruff format/check, and `ty` passed. Canonical default suite: 4315 passed, 3 failed, 45 deselected; total coverage 94.79%, branch coverage 91.09%. One failure is the separately owned T77 release-process regression. Two broker unit failures came from a sibling test orphan holding the authority lock; after its owner cleaned it up, all 72 tests in that module passed. The clean broker integration rerun is pending. Full engine suite was not run locally because Pandoc, Mermaid/Chromium, and LibreOffice are unavailable.
- Independent clean-context review approved implementation head `18bc5202f15fc02d760e21cadd5ec88891ec087d` without findings and explicitly approved the narrow final-image E2E exception: these are host-installed executables outside the final images, with real temporary-filesystem/process integration at the changed boundary. This does not waive T74's outstanding real-cluster matrix. CI run `35470822180` passed light, container/final-image verification, and gate checks. The latest main merge adds only T80's closure record; refreshed-head review and CI remain pending.

## Synchronization

Keep Linear and this mirror synchronized. Mark Done only after verification on main.
