---
ticket: T78
linear_id: G1L-577
linear_url: https://linear.app/g1lom/issue/G1L-577/t78-make-node-asset-permission-tests-independent-of-checkout-modes
status: Done
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
- 2026-09-20: Verified PR [#238](https://github.com/Guillaume-Lombardo/simple-md-to-docx-converter/pull/238) on main at squash commit `6808d2245de6e3a0113232966ce4b6d4f4cc3cc6`. The affected files match independently reviewed head `7c89da8901c6265b3a74c0c0106a233c915a8429`; all 23 focused tests passed again on merged main. Linear G1L-577 was then marked Done and re-fetched to confirm.
- Validation: `uv sync --all-groups`, Ruff formatting/lint, and `ty` passed. Canonical default suite: 4315 passed, 3 failed, 45 deselected; total coverage 94.79%, branch coverage 91.09%. One failure was the separately owned T77 release-process regression. Two broker unit failures came from a sibling test orphan holding the authority lock; after owner cleanup, the affected module passed 72/72. The isolated broker process, Podman, and node-installation integration rerun passed 59/59. Full engine suite was not run locally because Pandoc, Mermaid/Chromium, and LibreOffice were unavailable.
- Independent clean-context review approved the exact final head without findings and explicitly approved the narrow final-image E2E exception: these host-installed executables are outside the final images, and real temporary-filesystem/process integration covers the changed boundary. This does not waive T74's outstanding real-cluster matrix. Final-head CI run `35472254727` passed light, container/final-image verification, and gate checks. Source branch cleanup was verified locally and remotely; no live-cluster change occurred.
- Integrated validation: after T77 and T78 were incorporated into T79's final candidate, the canonical default suite passed all 4,339 tests with 45 engine-marked tests deselected, 94.89% overall coverage, and 91.22% application branch coverage. This clean run supersedes the earlier classified failures.

## Synchronization

Keep Linear and this mirror synchronized. Mark Done only after verification on main.
