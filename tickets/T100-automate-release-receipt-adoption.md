---
ticket: T100
linear_id: G1L-619
linear_url: https://linear.app/g1lom/issue/G1L-619/t100-automate-published-image-receipt-adoption-after-a-release
status: Backlog
priority: Medium
project: Markdown to DOCX and PDF Converter
---

# T100 - Automate published image receipt adoption after a release

## Objective

Remove the manual follow-up pull request that adopts the published GHCR receipt digests after each release by having the release workflow open it automatically from verified publication evidence.

Created from the repository analysis accepted by the owner on 2026-10-09 (section: what to change, item 6).

## Acceptance criteria

- After a successful PyPI and GHCR publication, the workflow generates the Compose and quickstart pin changes only from the verified registry receipts; it never infers or pre-pins an unpublished digest.
- It opens a pull request with the evidence attached; the PR still passes the normal required checks and independent review before squash merge.
- Failure or partial publication produces no PR and a clear workflow summary.
- Update `docs/releasing.md` and the AGENTS.md release rule accordingly.
- Test the generation logic with unit tests and a dry-run workflow path.

## Dependencies

- None.

## Blocking points

- A pull request created with the default `GITHUB_TOKEN` does not trigger the required CI workflows; a GitHub App or fine-grained token with reviewed permissions is required. Owner action on repository secrets is needed.

## Progress

- 2026-10-09: Created in Linear and mirrored here from the owner-accepted repository analysis.
  Not started.

## Synchronization

Keep status, scope, acceptance criteria, dependencies, and progress aligned with G1L-619.
